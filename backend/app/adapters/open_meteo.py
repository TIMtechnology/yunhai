from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.adapters.http_retry import get_json
from app.config import settings
from app.engine.utils import SHANGHAI_TZ, parse_shanghai_time
from app.services.cache import cache_get, cache_set

FORECAST_PRIMARY_TIMEOUT = 8.0
FORECAST_STALE_TTL = 86400 * 7

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SINGLE_RUN_FORECAST_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
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
    "wind_direction_10m",
    "wind_gusts_10m",
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

# Must stay aligned with scripts/backfill_forecast_archive.py. The reviewed
# operational model was trained on these surface fields from ECMWF IFS HRES
# 00Z single runs, not on Open-Meteo's generic best-match forecast.
OPERATIONAL_HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "visibility",
]

PROFILE_LEVELS = [1000, 975, 950, 925, 900, 850, 700, 500]
PROFILE_HOURLY_VARS = [
    *(f"cloud_cover_{level}hPa" for level in PROFILE_LEVELS),
    *(f"relative_humidity_{level}hPa" for level in PROFILE_LEVELS),
    *(f"geopotential_height_{level}hPa" for level in PROFILE_LEVELS),
]


async def _fetch_forecast_primary(lat: float, lng: float, days: int) -> dict:
    params = {
        "latitude": lat,
        "longitude": lng,
        "hourly": ",".join(HOURLY_VARS),
        "daily": "sunrise,sunset",
        "forecast_days": str(days),
        "timezone": "Asia/Shanghai",
    }
    return await get_json(
        FORECAST_URL,
        params=params,
        timeout=FORECAST_PRIMARY_TIMEOUT,
        retries=1,
    )


async def _fetch_forecast_historical_window(lat: float, lng: float, days: int) -> dict:
    from app.adapters.open_meteo_historical import fetch_historical_forecast

    start = datetime.now(SHANGHAI_TZ).date()
    end = start + timedelta(days=max(days - 1, 0))
    return await fetch_historical_forecast(lat, lng, start, end)


async def fetch_forecast(lat: float, lng: float, days: int = 5) -> dict:
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    cache_key = f"forecast:v6:{lat:.4f}:{lng:.4f}:{days}:{today}"
    stale_key = f"{cache_key}:stale"
    cached = cache_get(cache_key)
    if cached:
        return cached

    primary = asyncio.create_task(_fetch_forecast_primary(lat, lng, days))
    historical = asyncio.create_task(_fetch_forecast_historical_window(lat, lng, days))
    data: dict | None = None
    pending: set[asyncio.Task] = {primary, historical}
    try:
        while pending and data is None:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                try:
                    candidate = task.result()
                except Exception:
                    continue
                if candidate:
                    data = candidate
                    break
    finally:
        unfinished = [task for task in (primary, historical) if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)

    if data is None:
        stale = cache_get(stale_key)
        if stale:
            return stale
        raise httpx.TimeoutException("forecast fetch failed")

    cache_set(cache_key, data)
    cache_set(stale_key, data, ttl=FORECAST_STALE_TTL)
    return data


def operational_single_run_initialization(target_date: date) -> str:
    return f"{(target_date - timedelta(days=1)).isoformat()}T00:00"


def operational_single_run_source(target_date: date) -> str:
    return f"open_meteo_single_runs:ecmwf_ifs:run={operational_single_run_initialization(target_date)}"


async def fetch_operational_single_run(
    lat: float,
    lng: float,
    *,
    target_date: date,
) -> dict:
    """Fetch the exact as-issued model family used to train operational ML."""
    run = operational_single_run_initialization(target_date)
    cache_key = f"operational_single_run:v1:{lat:.4f}:{lng:.4f}:{run}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lng,
        "run": run,
        "forecast_hours": 48,
        "hourly": ",".join(OPERATIONAL_HOURLY_VARS),
        "timezone": "Asia/Shanghai",
        "models": "ecmwf_ifs",
    }
    data = await get_json(
        SINGLE_RUN_FORECAST_URL,
        params=params,
        timeout=FORECAST_PRIMARY_TIMEOUT,
        retries=1,
    )
    cache_set(cache_key, data, ttl=FORECAST_STALE_TTL)
    return data


async def fetch_profile_forecast(lat: float, lng: float, days: int = 5) -> dict:
    """Fetch pressure-level cloud profile data separately from the main prediction path."""
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    cache_key = f"profile_forecast:v1:{lat:.4f}:{lng:.4f}:{days}:{today}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lng,
        "hourly": ",".join(PROFILE_HOURLY_VARS + ["temperature_2m", "dew_point_2m"]),
        "forecast_days": days,
        "timezone": "Asia/Shanghai",
    }
    data = await get_json(FORECAST_URL, params=params, timeout=FORECAST_PRIMARY_TIMEOUT)

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


ELEVATION_BATCH_CHUNK = 60
ELEVATION_MAX_RETRIES = 4


async def _fetch_elevation_chunk(
    client: httpx.AsyncClient,
    chunk: list[tuple[float, float]],
) -> list[float]:
    params = {
        "latitude": ",".join(str(p[0]) for p in chunk),
        "longitude": ",".join(str(p[1]) for p in chunk),
    }
    for attempt in range(ELEVATION_MAX_RETRIES):
        resp = await client.get(ELEVATION_URL, params=params)
        if resp.status_code == 429 and attempt < ELEVATION_MAX_RETRIES - 1:
            await asyncio.sleep(min(2 ** attempt + 1, 12))
            continue
        resp.raise_for_status()
        return [float(x) for x in resp.json()["elevation"]]
    raise httpx.HTTPStatusError(
        "elevation rate limit",
        request=resp.request,
        response=resp,
    )


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
        try:
            async with httpx.AsyncClient(timeout=FORECAST_PRIMARY_TIMEOUT, http2=False) as client:
                for start in range(0, len(missing), ELEVATION_BATCH_CHUNK):
                    chunk = missing[start : start + ELEVATION_BATCH_CHUNK]
                    values = await _fetch_elevation_chunk(client, chunk)
                    for p, elev in zip(chunk, values):
                        val = float(elev)
                        cached_map[p] = val
                        cache_set(f"elev:{p[0]:.5f}:{p[1]:.5f}", val, ttl=86400 * 7)
        except (
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.TimeoutException,
        ):
            from app.adapters.open_meteo_historical import fetch_historical_forecast

            start = datetime.now(SHANGHAI_TZ).date()
            for p in missing:
                if p in cached_map:
                    continue
                try:
                    data = await fetch_historical_forecast(p[0], p[1], start, start)
                    raw_elevation = data.get("elevation")
                    if raw_elevation is None:
                        continue
                    val = float(raw_elevation)
                except Exception:
                    continue
                cached_map[p] = val
                cache_set(f"elev:{p[0]:.5f}:{p[1]:.5f}", val, ttl=86400 * 7)

    unresolved = [p for p in pairs if p not in cached_map]
    if unresolved:
        raise httpx.TimeoutException("elevation fetch failed")

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
