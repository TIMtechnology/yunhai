"""重点点位气象 watcher：在无用户访问且预报变化时主动跑预测并落库 snapshot。"""
from __future__ import annotations

import asyncio
from datetime import date as date_cls, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.adapters.open_meteo import fetch_forecast, slice_hourly_window
from app.config import settings
from app.services.label_session import build_predict_request
from app.services.predictor import run_prediction
from app.services.prediction_feedback import (
    PAGE_SOURCE_SCHEDULED,
    build_meteo_snapshot,
    meteo_change_significant,
    meteo_snapshot_unchanged,
)
from app.services.cloudsea_store import (
    get_latest_prediction_access_log,
    has_recent_user_prediction_access,
    list_watchlist_spots,
)

TZ = ZoneInfo("Asia/Shanghai")
WATCH_CLIENT_ID = "system-forecast-watch"


def is_watch_active_window(now: datetime | None = None) -> bool:
    """活跃窗：D-1 18:00 – D 07:59（按小时粗判）。"""
    hour = (now or datetime.now(TZ)).hour
    return hour >= 18 or hour < 8


def active_watch_target_dates(now: datetime | None = None) -> list[str]:
    """当前时刻应监控的日出目标日。"""
    now = now or datetime.now(TZ)
    today = now.date()
    hour = now.hour
    dates: list[str] = []
    if hour < 8:
        dates.append(today.isoformat())
    if hour >= 18:
        dates.append((today + timedelta(days=1)).isoformat())
    return dates


async def _fetch_hourly_for_spot(req) -> dict[str, Any]:
    wlat, wlng = req.lat, req.lng
    forecast = await fetch_forecast(wlat, wlng, days=5)
    return slice_hourly_window(forecast.get("hourly", {}), days=5)


async def maybe_run_scheduled_prediction(
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """对单点位单日判断是否应跑 scheduled predict。"""
    result: dict[str, Any] = {
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "target_date": target_date,
        "action": "skip",
        "reason": "",
    }
    if not settings.cloudsea_enabled or not settings.cloudsea_auto_snapshot:
        result["reason"] = "cloudsea snapshot disabled"
        return result

    try:
        req, _ = build_predict_request(spot_id=spot_id, viewpoint_id=viewpoint_id)
    except ValueError as exc:
        result["reason"] = str(exc)
        return result

    # label_session 默认 hours=24 不够覆盖「前夜 18:00 跑明日日出」；与线上一致用 120h
    req = req.model_copy(update={"hours": 120})

    hourly = await _fetch_hourly_for_spot(req)
    meteo_snapshot = build_meteo_snapshot(hourly, target_date)
    latest = get_latest_prediction_access_log(spot_id, viewpoint_id, target_date)

    if latest is None:
        result["reason"] = "baseline"
        result["action"] = "predict" if not dry_run else "would_predict"
    elif meteo_snapshot_unchanged(latest.get("meteo_snapshot"), meteo_snapshot):
        result["reason"] = "forecast_unchanged"
        return result
    elif not force and not meteo_change_significant(
        latest.get("meteo_snapshot"),
        meteo_snapshot,
        target_date,
        rh_delta_pp=settings.cloudsea_watch_rh_delta_pp,
        cloud_low_delta_pp=settings.cloudsea_watch_cloud_low_delta_pp,
    ):
        result["reason"] = "change_below_threshold"
        return result
    elif not force and has_recent_user_prediction_access(
        spot_id,
        viewpoint_id,
        target_date,
        within_minutes=settings.cloudsea_watch_user_quiet_minutes,
    ):
        result["reason"] = "recent_user_access"
        return result
    else:
        result["reason"] = "forecast_changed"
        result["action"] = "predict" if not dry_run else "would_predict"

    if dry_run or result["action"] != "predict":
        return result

    await run_prediction(
        req,
        page_source=PAGE_SOURCE_SCHEDULED,
        client_id=WATCH_CLIENT_ID,
        snapshot_target_dates=[target_date],
    )
    result["action"] = "predicted"
    return result


async def run_forecast_watch_cycle(
    *,
    label_days: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
) -> dict[str, Any]:
    """一轮 watcher：扫描 watchlist 并在活跃窗内触发 scheduled predict。"""
    now = datetime.now(TZ)
    days = label_days if label_days is not None else settings.cloudsea_watch_label_days
    summary: dict[str, Any] = {
        "at": now.isoformat(timespec="seconds"),
        "active_window": is_watch_active_window(now),
        "target_dates": active_watch_target_dates(now),
        "label_days": days,
        "dry_run": dry_run,
        "force": force,
        "checked": 0,
        "predicted": 0,
        "skipped": 0,
        "errors": 0,
        "results": [],
    }

    if not settings.cloudsea_watch_enabled and not force:
        summary["skipped_reason"] = "watch_disabled"
        return summary

    if not summary["active_window"] and not force:
        summary["skipped_reason"] = "outside_active_window"
        return summary

    target_dates = list(summary["target_dates"])
    if force and not target_dates:
        today = now.date()
        target_dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
        summary["target_dates"] = target_dates
    if not target_dates:
        summary["skipped_reason"] = "no_target_dates"
        return summary

    if spot_id and viewpoint_id:
        watchlist = [(spot_id, viewpoint_id)]
    else:
        watchlist = list_watchlist_spots(label_days=days)

    for sid, vid in watchlist:
        for target_date in target_dates:
            summary["checked"] += 1
            try:
                item = await maybe_run_scheduled_prediction(
                    sid,
                    vid,
                    target_date,
                    force=force,
                    dry_run=dry_run,
                )
            except Exception as exc:
                summary["errors"] += 1
                item = {
                    "spot_id": sid,
                    "viewpoint_id": vid,
                    "target_date": target_date,
                    "action": "error",
                    "reason": str(exc),
                }
            summary["results"].append(item)
            action = item.get("action")
            if action in ("predicted", "would_predict"):
                summary["predicted"] += 1
            elif action == "error":
                pass
            else:
                summary["skipped"] += 1

            if not dry_run and action == "predicted":
                await asyncio.sleep(0.5)

    return summary


def run_forecast_watch_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_forecast_watch_cycle(**kwargs))
