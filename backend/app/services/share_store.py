from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.cache import cache_get, cache_set
from app.services.evidence_builder import build_evidence_context

TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(TZ)


def _snapshot_key(sid: str) -> str:
    return f"share:snapshot:{sid}"


def _rate_key(ip: str) -> str:
    return f"share:rate:{_now().strftime('%Y%m%d')}:{ip}"


def public_url(path: str) -> str:
    base = settings.public_base_url.rstrip("/") or "https://yunhai.timkj.com"
    base = base.replace("https://yunhai.timqian.com", "https://yunhai.timkj.com")
    return f"{base}{path}" if base else path


def _pick_day(prediction: dict[str, Any], date_key: str) -> dict[str, Any]:
    return next((d for d in prediction.get("days") or [] if d.get("date") == date_key), {})


def _pick_sunrise_hour(prediction: dict[str, Any], day: dict[str, Any]) -> dict[str, Any]:
    idx = day.get("sunrise_hour_index")
    hours = prediction.get("hours") or []
    if isinstance(idx, int) and 0 <= idx < len(hours):
        return hours[idx]
    date_key = day.get("date")
    return next((h for h in hours if date_key and str(h.get("time", "")).startswith(date_key)), {})


def _build_share_meteogram(prediction: dict[str, Any], date_key: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for hour in prediction.get("hours") or []:
        if not str(hour.get("time", "")).startswith(date_key):
            continue
        weather = hour.get("weather") or {}
        cloudsea = hour.get("cloudsea") or {}
        sunrise = hour.get("sunrise") or {}
        scenario = hour.get("scenario") or {}
        visibility = weather.get("visibility")
        rows.append(
            {
                "time": str(hour.get("time", ""))[11:16] or "--:--",
                "temp_c": weather.get("temperature"),
                "rh_pct": weather.get("humidity"),
                "precip_mm": weather.get("precipitation"),
                "cloud_low": weather.get("cloud_cover_low"),
                "cloud_mid": weather.get("cloud_cover_mid"),
                "cloud_high": weather.get("cloud_cover_high"),
                "wind_speed": weather.get("wind_speed"),
                "wind_gusts": weather.get("wind_gusts"),
                "wind_direction": weather.get("wind_direction"),
                "visibility_km": round(float(visibility) / 1000, 1) if visibility is not None else None,
                "cloudsea_pct": cloudsea.get("probability"),
                "sunrise_pct": sunrise.get("probability"),
                "combined_score": scenario.get("combined_score"),
            }
        )
    return {
        "kind": "daily_static_meteogram",
        "hours": rows[:24],
    }


def check_share_rate(ip: str) -> None:
    key = _rate_key(ip)
    count = int(cache_get(key) or 0)
    if count >= settings.share_daily_ip_limit:
        raise ValueError("今日分享生成次数已达上限")
    cache_set(key, count + 1, ttl=86400)


def create_share_snapshot(
    *,
    prediction: dict[str, Any],
    date_key: str,
    include_ai: bool = False,
    ai_brief: str | None = None,
    privacy: str = "hide_coords",
    requester_ip: str = "unknown",
) -> dict[str, Any]:
    check_share_rate(requester_ip)
    day = _pick_day(prediction, date_key)
    hour = _pick_sunrise_hour(prediction, day)
    loc = prediction.get("location") or {}
    cloudsea = hour.get("cloudsea") or {}
    sunrise = hour.get("sunrise") or {}
    scenario = hour.get("scenario") or {}
    evidence = build_evidence_context(prediction, date_key)
    created = _now()
    sid = "sh_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    hide_coords = privacy != "show_coords"
    location = {
        "display_name": loc.get("name") or "云海日出点位",
        "spot_id": loc.get("spot_id"),
        "viewpoint_id": loc.get("viewpoint_id"),
        "elevation_m": loc.get("elevation"),
        "viewing_mode": loc.get("viewing_mode"),
    }
    if hide_coords:
        location["lat_rounded"] = round(float(loc.get("lat") or 0), 2)
        location["lng_rounded"] = round(float(loc.get("lng") or 0), 2)
    else:
        location["lat"] = loc.get("lat")
        location["lng"] = loc.get("lng")

    snapshot = {
        "id": sid,
        "version": 1,
        "created_at": created.isoformat(timespec="seconds"),
        "expires_at": (created + timedelta(seconds=settings.share_snapshot_ttl)).isoformat(timespec="seconds"),
        "location": location,
        "target": {
            "date": date_key,
            "weekday": day.get("weekday"),
            "sunrise_time": day.get("sunrise_time"),
        },
        "scores": {
            "cloudsea_prob_pct": cloudsea.get("probability"),
            "cloudsea_grade": cloudsea.get("grade"),
            "sunrise_prob_pct": sunrise.get("probability"),
            "sunrise_grade": sunrise.get("grade"),
            "combined_score": scenario.get("combined_score"),
            "scenario_label": scenario.get("label"),
            "verdict": (evidence.get("verdict_hint") or {}).get("verdict"),
        },
        "evidence": evidence,
        "meteogram": _build_share_meteogram(prediction, date_key),
        "ai_brief_excerpt": (ai_brief or "")[:400] if include_ai else "",
        "forecast_meta": prediction.get("forecast_meta") or {},
        "url": public_url(f"/s/{sid}"),
        "og_image_url": public_url(f"/api/share/{sid}/og.png"),
    }
    cache_set(_snapshot_key(sid), snapshot, ttl=settings.share_snapshot_ttl)
    return snapshot


def get_share_snapshot(sid: str) -> dict[str, Any] | None:
    snap = cache_get(_snapshot_key(sid))
    return snap if isinstance(snap, dict) else None
