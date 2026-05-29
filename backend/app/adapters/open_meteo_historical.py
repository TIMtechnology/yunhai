from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from app.adapters.open_meteo import HOURLY_VARS, parse_daily_astronomy
from app.engine.utils import SHANGHAI_TZ, parse_shanghai_time
from app.services.cache import cache_get, cache_set

HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"


async def fetch_historical_forecast(
    lat: float,
    lng: float,
    start_date: date,
    end_date: date,
) -> dict:
    cache_key = (
        f"hist_forecast:v1:{lat:.4f}:{lng:.4f}:"
        f"{start_date.isoformat()}:{end_date.isoformat()}"
    )
    cached = cache_get(cache_key)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lng,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": ",".join(HOURLY_VARS),
        "daily": "sunrise,sunset",
        "timezone": "Asia/Shanghai",
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.get(HISTORICAL_FORECAST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    cache_set(cache_key, data, ttl=86400 * 7)
    return data


def slice_hourly_for_date(hourly: dict, target_date: date) -> dict:
    """截取指定日历日 00:00–23:00 的逐小时数据。"""
    times: list[str] = hourly.get("time") or []
    if not times:
        return hourly

    start = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        tzinfo=SHANGHAI_TZ,
    )
    end = start + timedelta(days=1)
    keep = [
        i
        for i, t_str in enumerate(times)
        if start <= parse_shanghai_time(t_str) < end
    ]

    sliced: dict = {"time": [times[i] for i in keep]}
    for key, values in hourly.items():
        if key == "time" or not isinstance(values, list):
            continue
        sliced[key] = [values[i] for i in keep if i < len(values)]
    return sliced


def parse_astronomy_for_date(forecast: dict, target_date: date) -> dict[str, datetime]:
    astronomy = parse_daily_astronomy(forecast)
    return astronomy.get(target_date.isoformat(), {})
