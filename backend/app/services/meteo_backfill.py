"""标注日历史气象落库：训练/回测只读 DB，避免重复请求 Open-Meteo。"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.adapters.open_meteo import HOURLY_VARS
from app.adapters.open_meteo_historical import parse_astronomy_for_date
from app.engine.cloudsea_features import build_meteo_hour_row, meteo_row_complete
from app.engine.ml_eligibility import SUNRISE_WINDOW_END, SUNRISE_WINDOW_START
from app.services.cloudsea_store import save_meteo_day_cache, save_meteo_hourly
from app.services.meteo_cache import hour_rows_from_hourly, serialize_astronomy_for_store
from app.services.spot_loader import get_viewpoint

DEFAULT_COORDS: dict[tuple[str, str], tuple[float, float, float]] = {
    ("wunvshan", "dianjiangtai"): (41.31976, 125.40773, 804.0),
    ("donglingshan", "fengding"): (40.0161, 115.50136, 2274.0),
}


def resolve_label_coords(label: dict[str, Any]) -> tuple[float, float, float]:
    """从标注行 / 社区点位 / 精选观景点解析坐标。"""
    if label.get("lat") is not None and label.get("lng") is not None:
        elev = float(label["elevation"]) if label.get("elevation") is not None else 0.0
        return float(label["lat"]), float(label["lng"]), elev

    spot_id = str(label.get("spot_id") or "")
    viewpoint_id = str(label.get("viewpoint_id") or "")
    key = (spot_id, viewpoint_id)
    if key in DEFAULT_COORDS:
        return DEFAULT_COORDS[key]

    if spot_id == "community" and viewpoint_id:
        from app.services.community_store import get_community_location

        loc = get_community_location(viewpoint_id)
        if loc:
            return (
                float(loc["lat"]),
                float(loc["lng"]),
                float(loc.get("elevation") or 0),
            )

    if spot_id and viewpoint_id and spot_id != "community":
        vp = get_viewpoint(spot_id, viewpoint_id)
        if vp:
            return vp.lat, vp.lng, float(vp.elevation or 0)

    raise ValueError(f"无法解析坐标: {spot_id}/{viewpoint_id}")


PRECURSOR_EVENING_START = 20
PRECURSOR_DAWN_END = 8  # 不含 08:00，覆盖 0–7 点


def precursor_hour_keys(date_key: str) -> list[str]:
    """标注日 D 的 precursor 窗：D-1 20–23 + D 0–7。"""
    d = date_cls.fromisoformat(date_key)
    prev = (d - timedelta(days=1)).isoformat()
    keys = [f"{prev}T{h:02d}:00" for h in range(PRECURSOR_EVENING_START, 24)]
    keys += [f"{date_key}T{h:02d}:00" for h in range(0, PRECURSOR_DAWN_END)]
    return keys


def _filter_sunrise_window(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        time_str = str(row.get("time") or "")
        if "T" not in time_str:
            continue
        hour = int(time_str[11:13])
        if SUNRISE_WINDOW_START <= hour < SUNRISE_WINDOW_END:
            out.append(row)
    return sorted(out, key=lambda r: str(r.get("time")))


def _day_meteo_complete(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    db_path: Path | None = None,
    min_hours: int = 20,
) -> bool:
    rows = load_label_day_meteo(spot_id, viewpoint_id, date_key, db_path=db_path)
    complete = [r for r in rows if meteo_row_complete(r)]
    return len(complete) >= min_hours


def sunrise_window_meteo_complete(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    db_path: Path | None = None,
) -> bool:
    rows = load_label_day_meteo(spot_id, viewpoint_id, date_key, db_path=db_path)
    window = _filter_sunrise_window(rows)
    return len(window) >= 3 and all(meteo_row_complete(r) for r in window)


def precursor_window_meteo_complete(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    db_path: Path | None = None,
) -> bool:
    rows = load_label_precursor_meteo(spot_id, viewpoint_id, date_key, db_path=db_path)
    have = {str(r.get("time")) for r in rows if meteo_row_complete(r)}
    return all(k in have for k in precursor_hour_keys(date_key))


def load_label_day_meteo(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """优先 meteo_day_cache 全日，否则 meteo_hourly 逐时。"""
    from app.config import settings

    path = db_path or Path(settings.cloudsea_db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT hourly_json FROM meteo_day_cache
            WHERE spot_id=? AND viewpoint_id=? AND date_key=? AND source=?
            """,
            (spot_id, viewpoint_id, date_key, "historical_forecast"),
        ).fetchone()
        if row:
            hourly = json.loads(row["hourly_json"])
            return hour_rows_from_hourly(hourly, date_key)

        rows = conn.execute(
            """
            SELECT raw_json FROM meteo_hourly
            WHERE spot_id=? AND viewpoint_id=? AND ts LIKE ?
            ORDER BY ts
            """,
            (spot_id, viewpoint_id, f"{date_key}T%"),
        ).fetchall()
    finally:
        conn.close()

    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            parsed.append(raw)
    return parsed


def load_label_sunrise_meteo(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    return _filter_sunrise_window(
        load_label_day_meteo(spot_id, viewpoint_id, date_key, db_path=db_path)
    )


def load_label_precursor_meteo(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """D-1 20:00 至 D 07:00 逐时（跨日合并）。"""
    keys = set(precursor_hour_keys(date_key))
    d = date_cls.fromisoformat(date_key)
    prev = (d - timedelta(days=1)).isoformat()
    merged: list[dict[str, Any]] = []
    for dk in (prev, date_key):
        merged.extend(load_label_day_meteo(spot_id, viewpoint_id, dk, db_path=db_path))
    out = [r for r in merged if str(r.get("time") or "") in keys]
    return sorted(out, key=lambda r: str(r.get("time")))


def supplement_precursor_rows(
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    live_rows: list[dict[str, Any]],
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """用 DB 缓存/预报 archive 补全 live 预报缺失的 D-1 evening 等前体小时。"""
    from app.config import settings
    from app.services.cloudsea_store import load_forecast_archive_precursor

    keys = set(precursor_hour_keys(target_date))
    have = {str(r.get("time") or "") for r in live_rows}
    missing = keys - have
    if not missing:
        return live_rows

    path = db_path or Path(settings.cloudsea_db_path)
    by_time: dict[str, dict[str, Any]] = {str(r.get("time") or ""): r for r in live_rows}

    for row in load_forecast_archive_precursor(
        spot_id, viewpoint_id, target_date, db_path=path
    ):
        ts = str(row.get("time") or "")
        if ts in missing and ts not in by_time:
            by_time[ts] = row

    for row in load_label_precursor_meteo(spot_id, viewpoint_id, target_date, db_path=path):
        ts = str(row.get("time") or "")
        if ts in missing and ts not in by_time:
            by_time[ts] = row

    return sorted(by_time.values(), key=lambda r: str(r.get("time")))


def _days_to_backfill(
    date_key: str,
    spot_id: str,
    viewpoint_id: str,
    *,
    db_path: Path | None = None,
    force: bool = False,
) -> list[str]:
    d = date_cls.fromisoformat(date_key)
    prev = (d - timedelta(days=1)).isoformat()
    days: list[str] = []
    if force or not _day_meteo_complete(spot_id, viewpoint_id, date_key, db_path=db_path):
        days.append(date_key)
    if force or not _day_meteo_complete(spot_id, viewpoint_id, prev, db_path=db_path):
        days.append(prev)
    return days


def fetch_day_hourly_api(day: str, lat: float, lng: float) -> dict[str, Any]:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lng}&start_date={day}&end_date={day}"
        f"&hourly={','.join(HOURLY_VARS)}&timezone=Asia%2FShanghai"
    )
    with urllib.request.urlopen(url, timeout=90) as resp:
        return json.load(resp)


def persist_day_meteo(
    *,
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    lat: float,
    lng: float,
    elevation: float,
    payload: dict[str, Any],
    db_path: Path | None = None,
) -> int:
    """将 API 返回的整日 hourly 写入 meteo_hourly + meteo_day_cache。"""
    from app.config import settings

    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    path = db_path or Path(settings.cloudsea_db_path)
    conn = sqlite3.connect(path)
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    try:
        for idx, t_str in enumerate(times):
            if not str(t_str).startswith(date_key):
                continue
            raw = build_meteo_hour_row(hourly, idx)
            conn.execute(
                """
                INSERT INTO meteo_hourly
                (spot_id, viewpoint_id, lat, lng, elevation, ts, source, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lat, lng, ts, source) DO UPDATE SET
                    raw_json=excluded.raw_json,
                    spot_id=excluded.spot_id,
                    viewpoint_id=excluded.viewpoint_id
                """,
                (
                    spot_id,
                    viewpoint_id,
                    lat,
                    lng,
                    elevation,
                    t_str,
                    "historical_forecast",
                    json.dumps(raw, ensure_ascii=False),
                    now,
                ),
            )
            saved += 1
        conn.commit()
    finally:
        conn.close()

    astro = parse_astronomy_for_date(payload, date_cls.fromisoformat(date_key))
    astronomy = {date_key: astro} if astro else {}
    save_meteo_day_cache(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        date_key=date_key,
        source="historical_forecast",
        hourly=hourly,
        astronomy=serialize_astronomy_for_store(astronomy) if astronomy else None,
        db_path=path,
    )
    return saved


def backfill_label_meteo(
    label: dict[str, Any],
    *,
    db_path: Path | None = None,
    force: bool = False,
    sleep_sec: float = 0.35,
) -> dict[str, Any]:
    spot_id = str(label.get("spot_id") or "")
    viewpoint_id = str(label.get("viewpoint_id") or "")
    date_key = str(label.get("date") or "")
    if not spot_id or not viewpoint_id or not date_key:
        return {"status": "skip", "reason": "缺少 spot/viewpoint/date"}

    if not force and sunrise_window_meteo_complete(
        spot_id, viewpoint_id, date_key, db_path=db_path
    ) and precursor_window_meteo_complete(
        spot_id, viewpoint_id, date_key, db_path=db_path
    ):
        return {"status": "skipped", "date": date_key, "reason": "已完整"}

    lat, lng, elev = resolve_label_coords(label)
    days = _days_to_backfill(
        date_key, spot_id, viewpoint_id, db_path=db_path, force=force
    )
    if not days:
        days = [date_key]

    total_hours = 0
    for day in days:
        try:
            payload = fetch_day_hourly_api(day, lat, lng)
        except Exception as exc:
            if sleep_sec > 0:
                time.sleep(sleep_sec)
            return {"status": "failed", "date": date_key, "error": str(exc)}

        total_hours += persist_day_meteo(
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
            date_key=day,
            lat=lat,
            lng=lng,
            elevation=elev,
            payload=payload,
            db_path=db_path,
        )
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return {"status": "ok", "date": date_key, "hours_saved": total_hours, "days": days}


def backfill_all_labels(
    db_path: Path,
    *,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
    force: bool = False,
    sleep_sec: float = 0.35,
) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    clauses = ["spot_id IS NOT NULL", "viewpoint_id IS NOT NULL"]
    params: list[Any] = []
    if spot_id:
        clauses.append("spot_id=?")
        params.append(spot_id)
    if viewpoint_id:
        clauses.append("viewpoint_id=?")
        params.append(viewpoint_id)
    labels = conn.execute(
        f"""
        SELECT DISTINCT spot_id, viewpoint_id, date, lat, lng, location_id
        FROM cloudsea_labels
        WHERE {' AND '.join(clauses)}
        ORDER BY date
        """,
        params,
    ).fetchall()
    conn.close()

    updated = skipped = failed = 0
    for raw in labels:
        result = backfill_label_meteo(
            dict(raw),
            db_path=db_path,
            force=force,
            sleep_sec=sleep_sec,
        )
        status = result.get("status")
        if status == "ok":
            updated += 1
            print(f"  ok {result['date']} {raw['spot_id']}/{raw['viewpoint_id']} ({result.get('hours_saved')}h)")
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
            print(f"  fail {result.get('date')} {result.get('error') or result.get('reason')}")
    return {"updated": updated, "skipped": skipped, "failed": failed}


def ensure_label_meteo_cached(label: dict[str, Any]) -> None:
    """标注落库后补缓存（后台线程，不阻塞 API）；失败时重试一次。"""
    import logging
    import threading

    log = logging.getLogger(__name__)

    def _run() -> None:
        for attempt in range(2):
            try:
                result = backfill_label_meteo(label, force=False, sleep_sec=0.0)
                status = result.get("status")
                if status == "ok":
                    log.info(
                        "meteo backfill ok %s %s/%s",
                        result.get("date"),
                        label.get("spot_id"),
                        label.get("viewpoint_id"),
                    )
                    return
                if status == "skipped":
                    return
                log.warning(
                    "meteo backfill %s %s/%s attempt=%s: %s",
                    result.get("date"),
                    label.get("spot_id"),
                    label.get("viewpoint_id"),
                    attempt + 1,
                    result.get("error") or result.get("reason"),
                )
            except Exception as exc:
                log.warning(
                    "meteo backfill exception %s/%s attempt=%s: %s",
                    label.get("spot_id"),
                    label.get("viewpoint_id"),
                    attempt + 1,
                    exc,
                )

    threading.Thread(target=_run, daemon=True).start()
