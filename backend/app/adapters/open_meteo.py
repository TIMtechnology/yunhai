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
    # 850 / 925 / 700 hPa — Phase A 垂直场（Open-Meteo pressure-level hourly）
    "temperature_850hPa",
    "relative_humidity_850hPa",
    "temperature_925hPa",
    "relative_humidity_925hPa",
    "temperature_700hPa",
    "relative_humidity_700hPa",
    "temperature_500hPa",
    "relative_humidity_500hPa",
]


async def fetch_forecast(lat: float, lng: float, days: int = 5) -> dict:
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    cache_key = f"forecast:v5:{lat:.4f}:{lng:.4f}:{days}:{today}"
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

    elevs = await fetch_elevations_batch([lat], [lng])
    elevation = elevs[0]
    cache_set(cache_key, elevation, ttl=86400)
    return elevation


async def fetch_elevations_batch(lats: list[float], lngs: list[float]) -> list[float]:
    """批量海拔（Copernicus GLO-90，与 Open-Meteo Elevation API 一致）。"""
    if len(lats) != len(lngs):
        raise ValueError("lats/lngs length mismatch")
    if not lats:
        return []

    # 四舍五入减少重复请求
    pairs = [(round(a, 5), round(b, 5)) for a, b in zip(lats, lngs)]
    unique = list(dict.fromkeys(pairs))
    cached_map: dict[tuple[float, float], float] = {}
    missing: list[tuple[float, float]] = []
    for p in unique:
        ck = f"elev:{p[0]:.5f}:{p[1]:.5f}"
        hit = cache_get(ck)
        if hit is not None:
            cached_map[p] = float(hit)
        else:
            missing.append(p)

    if missing:
        params = {
            "latitude": ",".join(str(p[0]) for p in missing),
            "longitude": ",".join(str(p[1]) for p in missing),
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(ELEVATION_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        for p, elev in zip(missing, data["elevation"]):
            val = float(elev)
            cached_map[p] = val
            cache_set(f"elev:{p[0]:.5f}:{p[1]:.5f}", val, ttl=86400)

    return [cached_map[p] for p in pairs]


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
