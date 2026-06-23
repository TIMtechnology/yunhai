from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.engine.cloudsea_features import _segment_for_row
from app.engine.cloudsea_ml import resolve_ml_artifact
from app.engine.cloudsea_features import aggregate_v7_features, hour_raw_from_forecast
from app.models.schemas import PredictRequest, PredictResponse
from app.services.cloudsea_store import (
    get_label,
    get_latest_prediction_access_log,
    get_prediction_access_log,
    insert_prediction_access_log,
    list_access_log_ids_for_date,
    list_prediction_access_logs,
    list_unreconciled_access_logs,
    touch_prediction_access_log,
    upsert_prediction_access_outcome,
)
from app.services.meteo_backfill import load_label_day_meteo, load_label_precursor_meteo, precursor_hour_keys

TZ = ZoneInfo("Asia/Shanghai")
POSITIVE_THRESHOLD = 50


def _lead_hours_to_dawn(issue_time: datetime, target_date: str) -> float:
    dawn_start = datetime.fromisoformat(f"{target_date}T03:00:00").replace(tzinfo=TZ)
    return (dawn_start - issue_time.astimezone(TZ)).total_seconds() / 3600.0


def _compact_day_prediction(resp: PredictResponse, target_date: str) -> dict[str, Any]:
    day_summary = next((d for d in resp.days if d.date == target_date), None)
    window_hours = [
        {
            "time": h.time,
            "cloudsea_prob": h.cloudsea.probability,
            "cloudsea_grade": h.cloudsea.grade,
            "scenario_label": h.scenario.label,
            "combined_score": h.scenario.combined_score,
            "rh": h.weather.humidity,
            "cloud_low": h.weather.cloud_cover_low,
            "wind": h.weather.wind_speed,
        }
        for h in resp.hours
        if h.time[:10] == target_date and 3 <= int(h.time[11:13]) < 7
    ]
    peak_prob = day_summary.peak_cloudsea_prob if day_summary else None
    if peak_prob is None and window_hours:
        peak_prob = max(h["cloudsea_prob"] for h in window_hours)
    return {
        "target_date": target_date,
        "peak_cloudsea_prob": peak_prob,
        "sunrise_scenario_label": day_summary.sunrise_scenario_label if day_summary else None,
        "sunrise_combined_score": day_summary.sunrise_combined_score if day_summary else None,
        "window_hours": window_hours,
        "ml_status": resp.location.get("ml_status"),
    }


def _build_meteo_snapshot(hourly: dict[str, Any], target_date: str) -> dict[str, Any]:
    keys = set(precursor_hour_keys(target_date))
    times = hourly.get("time") or []
    cloud_low = hourly.get("cloud_cover_low") or []
    cloud_mid = hourly.get("cloud_cover_mid") or []
    cloud_high = hourly.get("cloud_cover_high") or []
    visibilities = hourly.get("visibility") or []
    rhs = hourly.get("relative_humidity_2m") or []
    rh_850_series = hourly.get("relative_humidity_850hPa") or []
    rh_700_series = hourly.get("relative_humidity_700hPa") or []
    t_850_series = hourly.get("temperature_850hPa") or []
    t_925_series = hourly.get("temperature_925hPa") or []
    winds = hourly.get("wind_speed_10m") or []
    precips = hourly.get("precipitation") or []
    temps = hourly.get("temperature_2m") or []
    dews = hourly.get("dew_point_2m") or []

    rows: list[dict[str, Any]] = []
    for j, ts in enumerate(times):
        if ts not in keys:
            continue
        rows.append(
            hour_raw_from_forecast(
                t_str=ts,
                idx=j,
                cloud_low=cloud_low,
                cloud_mid=cloud_mid,
                cloud_high=cloud_high,
                visibilities=visibilities,
                rhs=rhs,
                rh_850_series=rh_850_series,
                rh_700_series=rh_700_series,
                t_850_series=t_850_series,
                t_925_series=t_925_series,
                winds=winds,
                precips=precips,
                temps=temps,
                dews=dews,
            )
        )
    return {
        "window_spec": "precursor_12h",
        "target_date": target_date,
        "hour_keys": precursor_hour_keys(target_date),
        "rows": rows,
        "fingerprint": _meteo_fingerprint(rows),
    }


def _meteo_fingerprint(rows: list[dict[str, Any]]) -> str:
    """precursor 窗逐时关键场指纹，用于判断 live forecast 是否变化。"""
    parts: list[str] = []
    for row in sorted(rows, key=lambda r: str(r.get("time") or "")):
        t = str(row.get("time") or "")
        rh = round(float(row.get("rh") or 0), 1)
        low = round(float(row.get("cloud_low") or 0), 1)
        wind = round(float(row.get("wind_speed") or 0), 2)
        vis_raw = row.get("visibility")
        vis = round(float(vis_raw), 0) if vis_raw is not None else "na"
        precip = round(float(row.get("precipitation") or 0), 2)
        parts.append(f"{t}|{rh}|{low}|{wind}|{vis}|{precip}")
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return digest[:16]


def _visit_record(
    *,
    created_at: str,
    lead_hours: float | None,
    page_source: str | None,
    peak_prob: float | None,
) -> dict[str, Any]:
    return {
        "at": created_at,
        "lead_hours": lead_hours,
        "page_source": page_source,
        "peak_cloudsea_prob": peak_prob,
    }


def _append_access_visit(
    prediction: dict[str, Any],
    visit: dict[str, Any],
    *,
    min_gap_seconds: int = 60,
) -> dict[str, Any]:
    out = dict(prediction)
    visits: list[dict[str, Any]] = list(out.get("access_visits") or [])
    if visits:
        try:
            last_at = datetime.fromisoformat(str(visits[-1]["at"]))
            new_at = datetime.fromisoformat(str(visit["at"]))
            if abs((new_at - last_at).total_seconds()) < min_gap_seconds:
                return out
        except ValueError:
            pass
    visits.append(visit)
    out["access_visits"] = visits
    out["access_visit_count"] = len(visits)
    return out


def _same_meteo_snapshot(prev: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not prev:
        return False
    prev_fp = prev.get("fingerprint")
    cur_fp = current.get("fingerprint")
    if prev_fp and cur_fp:
        return prev_fp == cur_fp
    return _meteo_fingerprint(prev.get("rows") or []) == _meteo_fingerprint(current.get("rows") or [])


def _expand_history_entries(log: dict[str, Any], prediction: dict[str, Any]) -> list[dict[str, Any]]:
    visits = prediction.get("access_visits") or []
    base = {
        "log_id": log["id"],
        "peak_cloudsea_prob": prediction.get("peak_cloudsea_prob"),
        "model_version": log.get("model_version"),
        "direction_ok": log.get("direction_ok"),
        "diagnosis": log.get("diagnosis"),
        "predicted_positive": log.get("predicted_positive"),
    }
    if len(visits) <= 1:
        return [
            {
                **base,
                "id": log["id"],
                "created_at": log["created_at"],
                "lead_hours_to_dawn": log.get("lead_hours_to_dawn"),
                "page_source": log.get("page_source"),
                "same_forecast": False,
            }
        ]
    entries: list[dict[str, Any]] = []
    for i, v in enumerate(visits):
        entries.append(
            {
                **base,
                "id": log["id"] if i == 0 else f"{log['id']}:{i}",
                "created_at": v.get("at") or log["created_at"],
                "lead_hours_to_dawn": v.get("lead_hours", log.get("lead_hours_to_dawn")),
                "page_source": v.get("page_source", log.get("page_source")),
                "peak_cloudsea_prob": v.get("peak_cloudsea_prob", prediction.get("peak_cloudsea_prob")),
                "same_forecast": i > 0,
            }
        )
    return entries


def _model_version(spot_id: str | None, viewpoint_id: str | None) -> str:
    artifact = resolve_ml_artifact(spot_id, viewpoint_id) if spot_id and viewpoint_id else None
    if artifact:
        window = artifact.get("window") or "sunrise"
        n_days = artifact.get("n_days") or "?"
        return f"ml_{window}_n{n_days}"
    return "rule_only"


def _maybe_feature_snapshot(
    *,
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    meteo_snapshot: dict[str, Any],
    elevation: float,
    terrain: dict | None,
) -> dict[str, Any] | None:
    artifact = resolve_ml_artifact(spot_id, viewpoint_id)
    if not artifact or artifact.get("window") != "v7":
        return None
    rows = meteo_snapshot.get("rows") or []
    if not rows:
        return None
    feature_names = artifact.get("feature_names") or []
    use_observable = any(n in feature_names for n in ("observable_fraction_mean",))
    feat = aggregate_v7_features(
        rows,
        target_date=target_date,
        elevation=elevation,
        terrain=terrain,
        use_observable_field=use_observable,
    )
    return {"feature_names": list(feat.keys()), "values": feat}


def log_prediction_access_sync(
    *,
    req: PredictRequest,
    resp: PredictResponse,
    hourly: dict[str, Any],
    elevation: float,
    terrain: dict | None,
    issue_time: datetime,
    page_source: str | None = None,
    client_id: str | None = None,
    data_source: str = "live_forecast",
) -> None:
    if not settings.cloudsea_enabled or not settings.cloudsea_auto_snapshot:
        return
    if not req.spot_id or not req.viewpoint_id:
        return

    target_dates = sorted({d.date for d in resp.days if d.date})
    if not target_dates:
        target_dates = sorted({h.time[:10] for h in resp.hours if h.time})
    model_version = _model_version(req.spot_id, req.viewpoint_id)
    created_at = issue_time.astimezone(TZ).isoformat(timespec="seconds")

    for target_date in target_dates:
        try:
            meteo_snapshot = _build_meteo_snapshot(hourly, target_date)
            prediction = _compact_day_prediction(resp, target_date)
            lead = _lead_hours_to_dawn(issue_time, target_date)
            visit = _visit_record(
                created_at=created_at,
                lead_hours=lead,
                page_source=page_source,
                peak_prob=prediction.get("peak_cloudsea_prob"),
            )

            latest = get_latest_prediction_access_log(req.spot_id, req.viewpoint_id, target_date)
            if latest and _same_meteo_snapshot(latest.get("meteo_snapshot"), meteo_snapshot):
                try:
                    prev_pred = latest.get("prediction") or json.loads(latest.get("prediction_json") or "{}")
                except json.JSONDecodeError:
                    prev_pred = dict(prediction)
                merged_pred = _append_access_visit(prev_pred, visit)
                if prediction.get("peak_cloudsea_prob") is not None:
                    merged_pred["peak_cloudsea_prob"] = prediction["peak_cloudsea_prob"]
                touch_prediction_access_log(
                    int(latest["id"]),
                    created_at=created_at,
                    lead_hours_to_dawn=lead,
                    prediction=merged_pred,
                    page_source=page_source,
                    client_id=client_id,
                )
                continue

            prediction = _append_access_visit(prediction, visit)
            feature_snapshot = _maybe_feature_snapshot(
                spot_id=req.spot_id,
                viewpoint_id=req.viewpoint_id,
                target_date=target_date,
                meteo_snapshot=meteo_snapshot,
                elevation=elevation,
                terrain=terrain,
            )
            insert_prediction_access_log(
                created_at=created_at,
                target_date=target_date,
                lead_hours_to_dawn=lead,
                spot_id=req.spot_id,
                viewpoint_id=req.viewpoint_id,
                location_id=None,
                lat=req.lat,
                lng=req.lng,
                elevation=elevation,
                page_source=page_source,
                client_id=client_id,
                model_version=model_version,
                data_source=data_source,
                prediction=prediction,
                meteo_snapshot=meteo_snapshot,
                feature_snapshot=feature_snapshot,
            )
        except Exception:
            continue


def schedule_prediction_access_log(**kwargs: Any) -> None:
    if not settings.cloudsea_enabled or not settings.cloudsea_auto_snapshot:
        return
    thread = threading.Thread(
        target=log_prediction_access_sync,
        kwargs=kwargs,
        daemon=True,
        name="prediction-access-log",
    )
    thread.start()


def _segment_stats(rows: list[dict[str, Any]], target_date: str, segment: str) -> dict[str, float | None]:
    seg_rows = [r for r in rows if _segment_for_row(r, target_date) == segment]
    if not seg_rows:
        return {"rh_mean": None, "cloud_low_mean": None, "wind_mean": None, "count": 0}
    rh_vals = [float(r["rh"]) for r in seg_rows if r.get("rh") is not None]
    low_vals = [float(r["cloud_low"]) for r in seg_rows if r.get("cloud_low") is not None]
    wind_vals = [float(r["wind_speed"]) for r in seg_rows if r.get("wind_speed") is not None]
    return {
        "rh_mean": round(sum(rh_vals) / len(rh_vals), 2) if rh_vals else None,
        "cloud_low_mean": round(sum(low_vals) / len(low_vals), 2) if low_vals else None,
        "wind_mean": round(sum(wind_vals) / len(wind_vals), 2) if wind_vals else None,
        "count": len(seg_rows),
    }


def _hourly_errors(forecast_rows: list[dict], actual_rows: list[dict]) -> list[dict[str, Any]]:
    actual_by_time = {str(r.get("time")): r for r in actual_rows}
    errors: list[dict[str, Any]] = []
    for frow in forecast_rows:
        ts = str(frow.get("time") or "")
        arow = actual_by_time.get(ts)
        if not arow:
            continue
        err: dict[str, Any] = {"time": ts}
        for field in ("rh", "cloud_low", "wind_speed", "visibility"):
            fv, av = frow.get(field), arow.get(field)
            if fv is not None and av is not None:
                err[f"{field}_forecast"] = float(fv)
                err[f"{field}_actual"] = float(av)
                err[f"{field}_delta"] = round(float(av) - float(fv), 2)
        errors.append(err)
    return errors


def diagnose_outcome(
    *,
    forecast_rows: list[dict],
    actual_rows: list[dict],
    target_date: str,
    predicted_positive: bool,
    label_positive: bool,
    peak_prob: float | None,
) -> dict[str, Any]:
    tags: list[str] = []
    f_seg = {seg: _segment_stats(forecast_rows, target_date, seg) for seg in ("evening", "night", "dawn")}
    a_seg = {seg: _segment_stats(actual_rows, target_date, seg) for seg in ("evening", "night", "dawn")}

    f_delta_rh = None
    a_delta_rh = None
    if f_seg["night"]["rh_mean"] is not None and f_seg["dawn"]["rh_mean"] is not None:
        f_delta_rh = round(float(f_seg["dawn"]["rh_mean"]) - float(f_seg["night"]["rh_mean"]), 2)
    if a_seg["night"]["rh_mean"] is not None and a_seg["dawn"]["rh_mean"] is not None:
        a_delta_rh = round(float(a_seg["dawn"]["rh_mean"]) - float(a_seg["night"]["rh_mean"]), 2)

    summary_parts: list[str] = []
    if f_delta_rh is not None and a_delta_rh is not None:
        summary_parts.append(
            f"night→dawn ΔRH 预报{f_delta_rh:+.0f} 实况{a_delta_rh:+.0f}"
        )
        if f_delta_rh > 0 and a_delta_rh < -3:
            tags.append("dissipating")
            tags.append("overoptimistic_dawn")
        if a_delta_rh < -5 and predicted_positive and not label_positive:
            tags.append("night_dried")

    if predicted_positive and not label_positive:
        if f_seg["dawn"]["rh_mean"] and a_seg["dawn"]["rh_mean"]:
            if float(f_seg["dawn"]["rh_mean"]) - float(a_seg["dawn"]["rh_mean"]) >= 8:
                tags.append("overoptimistic_dawn")
        tags.append("false_positive")

    if not predicted_positive and label_positive:
        tags.append("false_negative")

    if f_seg["dawn"]["cloud_low_mean"] and a_seg["dawn"]["cloud_low_mean"]:
        cloud_gap = float(f_seg["dawn"]["cloud_low_mean"]) - float(a_seg["dawn"]["cloud_low_mean"])
        if abs(cloud_gap) >= 15:
            tags.append("process_mismatch")
            summary_parts.append(f"dawn 低云预报偏{'高' if cloud_gap > 0 else '低'} {abs(cloud_gap):.0f}%")

    if peak_prob is not None:
        summary_parts.insert(0, f"预测 P={peak_prob:.0f}%")

    return {
        "tags": sorted(set(tags)),
        "summary": "；".join(summary_parts) if summary_parts else "",
        "segments_forecast": f_seg,
        "segments_actual": a_seg,
        "delta_rh_night_to_dawn_forecast": f_delta_rh,
        "delta_rh_night_to_dawn_actual": a_delta_rh,
    }


def reconcile_access_log(access_log_id: int, *, force: bool = False) -> dict[str, Any] | None:
    row = get_prediction_access_log(access_log_id)
    if not row:
        return None
    if row.get("reconciled_at") and not force:
        return row

    spot_id = row.get("spot_id")
    viewpoint_id = row.get("viewpoint_id")
    target_date = row.get("target_date")
    if not spot_id or not viewpoint_id or not target_date:
        return None

    label = get_label(spot_id, viewpoint_id, target_date, 3, 7)
    try:
        meteo_snapshot = row.get("meteo_snapshot") or json.loads(row.get("meteo_snapshot_json") or "{}")
    except json.JSONDecodeError:
        meteo_snapshot = {}
    try:
        prediction = row.get("prediction") or json.loads(row.get("prediction_json") or "{}")
    except json.JSONDecodeError:
        prediction = {}

    forecast_rows = meteo_snapshot.get("rows") or []
    actual_precursor = load_label_precursor_meteo(spot_id, viewpoint_id, target_date)
    actual_day = load_label_day_meteo(spot_id, viewpoint_id, target_date)
    actual_meteo = {
        "precursor": actual_precursor,
        "day": actual_day,
    }

    peak_prob = prediction.get("peak_cloudsea_prob")
    if peak_prob is None:
        peak_prob = 0
    predicted_positive = int(float(peak_prob) >= POSITIVE_THRESHOLD)
    label_status = label["status"] if label else None
    label_positive = int(label_status in ("full", "partial")) if label_status else None
    direction_ok = None
    if label_positive is not None:
        direction_ok = int(predicted_positive == label_positive)

    forecast_error = {
        "hourly": _hourly_errors(forecast_rows, actual_precursor),
        "segments_forecast": {
            seg: _segment_stats(forecast_rows, target_date, seg) for seg in ("evening", "night", "dawn")
        },
        "segments_actual": {
            seg: _segment_stats(actual_precursor, target_date, seg) for seg in ("evening", "night", "dawn")
        },
    }
    diagnosis = diagnose_outcome(
        forecast_rows=forecast_rows,
        actual_rows=actual_precursor,
        target_date=target_date,
        predicted_positive=bool(predicted_positive),
        label_positive=bool(label_positive) if label_positive is not None else False,
        peak_prob=float(peak_prob),
    )

    upsert_prediction_access_outcome(
        access_log_id=access_log_id,
        label_status=label_status,
        label_id=label["id"] if label else None,
        actual_meteo=actual_meteo,
        forecast_error=forecast_error,
        predicted_positive=predicted_positive,
        label_positive=label_positive if label_positive is not None else 0,
        direction_ok=direction_ok,
        diagnosis=diagnosis,
    )
    return get_prediction_access_log(access_log_id)


def reconcile_target_date(target_date: str, *, force: bool = False) -> dict[str, Any]:
    if force:
        ids = list_access_log_ids_for_date(target_date)
    else:
        ids = [int(r["id"]) for r in list_unreconciled_access_logs(target_date=target_date, limit=2000)]
    reconciled = 0
    for log_id in ids:
        if reconcile_access_log(log_id, force=force):
            reconciled += 1
    return {"target_date": target_date, "reconciled": reconciled, "total": len(ids)}


def get_prediction_history(
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
) -> dict[str, Any]:
    logs = list_prediction_access_logs(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        target_date=target_date,
    )
    label = get_label(spot_id, viewpoint_id, target_date, 3, 7)
    actual_precursor = load_label_precursor_meteo(spot_id, viewpoint_id, target_date)

    entries: list[dict[str, Any]] = []
    correct = 0
    total_with_outcome = 0
    for log in logs:
        try:
            prediction = log.get("prediction") or json.loads(log.get("prediction_json") or "{}")
        except json.JSONDecodeError:
            prediction = {}
        if log.get("direction_ok") is not None:
            total_with_outcome += 1
            if log.get("direction_ok"):
                correct += 1
        entries.extend(_expand_history_entries(log, prediction))

    entries.sort(key=lambda e: str(e.get("created_at") or ""), reverse=True)

    return {
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "target_date": target_date,
        "label": label,
        "access_count": len(entries),
        "snapshot_count": len(logs),
        "correct_count": correct,
        "outcome_count": total_with_outcome,
        "entries": entries,
        "actual_precursor": actual_precursor,
    }
