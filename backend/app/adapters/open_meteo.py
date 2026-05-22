from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.engine.utils import SHANGHAI_TZ, parse_shanghai_time
from app.services.cache import cache_get, cache_set

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "wind_speed_10m",
    "visibility",
]


async def fetch_forecast(lat: float, lng: float, days: int = 5) -> dict:
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    cache_key = f"forecast:v4:{lat:.4f}:{lng:.4f}:{days}:{today}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lng,
        "hourly": ",".join(HOURLY_VARS),
        "daily": "sunrise,sunset",
        "forecast_days": days,
        "timezone": "Asia/Shanghai",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(FORECAST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    cache_set(cache_key, data)
    return data


async def fetch_elevation(lat: float, lng: float) -> float:
    cache_key = f"elev:{lat:.4f}:{lng:.4f}"
    cached = cache_get(cache_key)
    if cached is not None:
        return float(cached)

    params = {"latitude": lat, "longitude": lng}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(ELEVATION_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    elevation = float(data["elevation"][0])
    cache_set(cache_key, elevation, ttl=86400)
    return elevation


def estimate_cloud_base(temp_c: float, dewpoint_c: float) -> float:
    spread = max(temp_c - dewpoint_c, 0.1)
    return spread * 125.0


def parse_daily_astronomy(forecast: dict) -> dict[str, dict[str, datetime]]:
    """Parse Open-Meteo daily sunrise/sunset into date -> {sunrise, sunset}."""
    daily = forecast.get("daily") or {}
    dates = daily.get("time") or []
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    result: dict[str, dict[str, datetime]] = {}
    for i, date_key in enumerate(dates):
        entry: dict[str, datetime] = {}
        if i < len(sunrises) and sunrises[i]:
            entry["sunrise"] = parse_shanghai_time(sunrises[i])
        if i < len(sunsets) and sunsets[i]:
            entry["sunset"] = parse_shanghai_time(sunsets[i])
        if entry:
            result[date_key] = entry
    return result


def slice_hourly_window(hourly: dict, days: int = 5) -> dict:
    """截取今天 00:00 起连续 days 天的逐小时数据（非滚动 120h）。"""
    times: list[str] = hourly.get("time") or []
    if not times:
        return hourly

    start = datetime.now(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    keep = [
        i
        for i, t_str in enumerate(times)
        if start <= parse_shanghai_time(t_str) < end
    ][: days * 24]

    sliced: dict = {"time": [times[i] for i in keep]}
    for key, values in hourly.items():
        if key == "time" or not isinstance(values, list):
            continue
        sliced[key] = [values[i] for i in keep if i < len(values)]
    return sliced
