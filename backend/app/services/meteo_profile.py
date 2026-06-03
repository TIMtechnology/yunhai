from __future__ import annotations

from datetime import date as date_cls, datetime
from zoneinfo import ZoneInfo

from app.adapters.open_meteo import PROFILE_LEVELS, estimate_cloud_base, fetch_profile_forecast
from app.services.cache import cache_get, cache_set

TZ = ZoneInfo("Asia/Shanghai")


def _value(series: list, idx: int):
    return series[idx] if idx < len(series) else None


async def build_meteo_profile(
    *,
    lat: float,
    lng: float,
    date_key: str,
    elevation: float | None = None,
    days: int = 5,
) -> dict:
    """Build a time x height cloud profile for Meteogram display."""
    target = date_cls.fromisoformat(date_key)
    today = datetime.now(TZ).date()
    forecast_days = min(max((target - today).days + 1, days), 16) if target >= today else days
    cache_key = f"meteo_profile:v1:{lat:.4f}:{lng:.4f}:{date_key}:{forecast_days}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    forecast = await fetch_profile_forecast(lat, lng, days=forecast_days)
    hourly = forecast.get("hourly") or {}
    times: list[str] = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    dews = hourly.get("dew_point_2m") or []
    hours: list[dict] = []

    for idx, t_str in enumerate(times):
        if not str(t_str).startswith(date_key):
            continue
        temp = _value(temps, idx)
        dew = _value(dews, idx)
        levels: list[dict] = []
        for level in PROFILE_LEVELS:
            cover = _value(hourly.get(f"cloud_cover_{level}hPa") or [], idx)
            rh = _value(hourly.get(f"relative_humidity_{level}hPa") or [], idx)
            height = _value(hourly.get(f"geopotential_height_{level}hPa") or [], idx)
            if height is None:
                continue
            levels.append(
                {
                    "pressure_hpa": level,
                    "height_m_asl": round(float(height), 0),
                    "cloud_cover_pct": round(float(cover), 0) if cover is not None else None,
                    "rh_pct": round(float(rh), 0) if rh is not None else None,
                }
            )
        hours.append(
            {
                "time": t_str,
                "levels": sorted(levels, key=lambda x: x["height_m_asl"]),
                "viewpoint_elevation_m": elevation,
                "cloud_base_estimate_m": (
                    round(estimate_cloud_base(float(temp), float(dew)) + (elevation or 0), 0)
                    if temp is not None and dew is not None
                    else None
                ),
            }
        )

    payload = {
        "date": date_key,
        "source": "open-meteo",
        "model_note": "pressure-level cloud cover estimated from model humidity fields",
        "lat": lat,
        "lng": lng,
        "elevation": elevation,
        "hours": hours,
        "levels_hpa": PROFILE_LEVELS,
    }
    cache_set(cache_key, payload, ttl=3600)
    return payload
