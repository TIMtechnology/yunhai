"""日出扇区多点气象：Open-Meteo 批量采样可视范围内各 GPS 网格云量。"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.adapters.http_retry import get_json
from app.adapters.open_meteo import (
    FORECAST_PRIMARY_TIMEOUT,
    FORECAST_URL,
    SINGLE_RUN_FORECAST_URL,
    estimate_cloud_base,
    operational_single_run_initialization,
)

# 扇区仅需低层云量/温湿，不必拉全量气压层（显著减小响应体）
SECTOR_HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "cloud_cover_low",
    "cloud_cover_mid",
    "visibility",
]
from app.adapters.open_meteo_historical import HISTORICAL_FORECAST_URL
from app.adapters.dem import estimate_cloud_top_m
from app.services.cache import cache_get, cache_set

DEFAULT_SAMPLE_DISTANCES_KM = (4.0, 9.0, 14.0, 18.0)
VALLEY_SAMPLE_DISTANCES_KM = (2.5, 5.0, 7.5, 10.0)


def _angular_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def pick_sector_sample_points(
    elev_profile: list[dict[str, Any]],
    *,
    sunrise_azimuth_deg: float,
    visible_range_km: float,
    az_tolerance_deg: float = 8.0,
    target_distances_km: tuple[float, ...] = DEFAULT_SAMPLE_DISTANCES_KM,
) -> list[dict[str, Any]]:
    """从 DEM 剖面中选取中心射线上的代表点（用于批量拉取 NWP）。"""
    candidates = [
        p
        for p in elev_profile
        if float(p.get("distance_km") or 0) > 0
        and float(p.get("distance_km") or 0) <= visible_range_km
        and _angular_diff(float(p.get("azimuth_deg") or sunrise_azimuth_deg), sunrise_azimuth_deg)
        <= az_tolerance_deg
    ]
    if not candidates:
        return []

    picked: list[dict[str, Any]] = []
    used: set[tuple[float, float]] = set()
    for target in target_distances_km:
        if target > visible_range_km:
            continue
        best = min(candidates, key=lambda p: abs(float(p["distance_km"]) - target))
        key = (round(float(best["lat"]), 5), round(float(best["lng"]), 5))
        if key in used:
            continue
        used.add(key)
        picked.append(dict(best))
    return picked


def pick_valley_sample_points(
    elev_profile: list[dict[str, Any]],
    *,
    viewer_elev_m: float,
    visible_range_km: float = 10.0,
    min_drop_m: float = 120.0,
    target_distances_km: tuple[float, ...] = VALLEY_SAMPLE_DISTANCES_KM,
) -> list[dict[str, Any]]:
    """Select low terrain cells below the viewpoint for valley-fill weather sampling."""
    candidates = [
        p
        for p in elev_profile
        if 0 < float(p.get("distance_km") or 0) <= visible_range_km
        and float(p.get("elev_m") or viewer_elev_m) <= viewer_elev_m - min_drop_m
    ]
    if not candidates:
        return []

    picked: list[dict[str, Any]] = []
    used: set[tuple[float, float]] = set()
    for target in target_distances_km:
        band = [
            p
            for p in candidates
            if abs(float(p.get("distance_km") or 0) - target) <= 1.4
        ]
        if not band:
            continue
        best = min(
            band,
            key=lambda p: (
                float(p.get("elev_m") or viewer_elev_m),
                abs(float(p.get("distance_km") or 0) - target),
            ),
        )
        key = (round(float(best["lat"]), 5), round(float(best["lng"]), 5))
        if key in used:
            continue
        used.add(key)
        picked.append({**best, "sample_role": "valley"})
    return picked


async def _fetch_multi_forecast(
    lats: list[float],
    lngs: list[float],
    *,
    url: str,
    extra_params: dict[str, str],
    include_daily: bool = True,
) -> list[dict[str, Any]]:
    if not lats:
        return []
    params = {
        "latitude": ",".join(str(x) for x in lats),
        "longitude": ",".join(str(x) for x in lngs),
        "hourly": ",".join(SECTOR_HOURLY_VARS),
        "timezone": "Asia/Shanghai",
        **extra_params,
    }
    if include_daily:
        params["daily"] = "sunrise,sunset"
    try:
        data = await get_json(url, params=params, timeout=FORECAST_PRIMARY_TIMEOUT, retries=1)
    except (
        httpx.ConnectError,
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.TimeoutException,
    ):
        if url == FORECAST_URL and "forecast_days" in extra_params:
            from app.engine.utils import SHANGHAI_TZ

            days = int(extra_params["forecast_days"])
            start = datetime.now(SHANGHAI_TZ).date()
            end = start + timedelta(days=max(days - 1, 0))
            hist_params = {
                **params,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
            hist_params.pop("forecast_days", None)
            data = await get_json(HISTORICAL_FORECAST_URL, params=hist_params, timeout=20.0, retries=2)
        else:
            raise
    if isinstance(data, list):
        return data
    return [data]


async def fetch_sector_forecast_multi(
    points: list[dict[str, Any]],
    *,
    days: int = 5,
) -> list[dict[str, Any]]:
    if not points:
        return []
    lats = [float(p["lat"]) for p in points]
    lngs = [float(p["lng"]) for p in points]
    key = f"sector_fc:v1:{','.join(f'{a:.4f},{b:.4f}' for a,b in zip(lats,lngs))}:{days}"
    cached = cache_get(key)
    if cached:
        return list(cached)
    data = await _fetch_multi_forecast(
        lats,
        lngs,
        url=FORECAST_URL,
        extra_params={"forecast_days": str(days)},
    )
    cache_set(key, data, ttl=3600)
    return data


async def fetch_sector_historical_multi(
    points: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    if not points:
        return []
    lats = [float(p["lat"]) for p in points]
    lngs = [float(p["lng"]) for p in points]
    key = (
        f"sector_hist:v1:{start_date}:{end_date}:"
        f"{','.join(f'{a:.4f},{b:.4f}' for a,b in zip(lats,lngs))}"
    )
    cached = cache_get(key)
    if cached:
        return list(cached)
    data = await _fetch_multi_forecast(
        lats,
        lngs,
        url=HISTORICAL_FORECAST_URL,
        extra_params={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )
    if data:
        cache_set(key, data, ttl=86400 * 7)
    return data


async def fetch_sector_operational_multi(
    points: list[dict[str, Any]],
    *,
    target_date: date,
) -> list[dict[str, Any]]:
    """Fetch the same D-1 ECMWF IFS 00Z run used by the reviewed point model."""
    if not points:
        return []
    lats = [float(p["lat"]) for p in points]
    lngs = [float(p["lng"]) for p in points]
    run = operational_single_run_initialization(target_date)
    key = (
        f"sector_operational:v1:{run}:"
        f"{','.join(f'{a:.4f},{b:.4f}' for a, b in zip(lats, lngs))}"
    )
    cached = cache_get(key)
    if cached:
        return list(cached)
    data = await _fetch_multi_forecast(
        lats,
        lngs,
        url=SINGLE_RUN_FORECAST_URL,
        extra_params={
            "run": run,
            "models": "ecmwf_ifs",
            "forecast_hours": "48",
        },
        include_daily=False,
    )
    if data:
        cache_set(key, data, ttl=86400 * 7)
    return data


def _hour_snapshot(hourly: dict[str, Any], idx: int) -> dict[str, Any]:
    def _v(key: str, default=None):
        arr = hourly.get(key) or []
        if idx >= len(arr):
            return default
        return arr[idx]

    temp = _v("temperature_2m")
    dew = _v("dew_point_2m")
    cloud_low = float(_v("cloud_cover_low") or 0)
    cloud_mid = float(_v("cloud_cover_mid") or 0)
    rh = float(_v("relative_humidity_2m") or 70)
    vis = _v("visibility")
    precip = float(_v("precipitation") or 0)
    base = estimate_cloud_base(float(temp or 10), float(dew or 8)) if temp is not None and dew is not None else None
    top = (
        estimate_cloud_top_m(base, cloud_low, cloud_mid)
        if base is not None
        else None
    )
    return {
        "cloud_low": cloud_low,
        "cloud_mid": cloud_mid,
        "rh": rh,
        "visibility": vis,
        "precipitation": precip,
        "cloud_base_m": base,
        "cloud_top_m": top,
        "temp": temp,
        "dewpoint": dew,
    }


def build_sector_meteo_index(
    forecasts: list[dict[str, Any]],
    sample_points: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """time_str -> 各采样点该时刻气象快照（含 lat/lng/elev）。"""
    index: dict[str, list[dict[str, Any]]] = {}
    for fc, pt in zip(forecasts, sample_points):
        hourly = fc.get("hourly") or {}
        times = hourly.get("time") or []
        for idx, t_str in enumerate(times):
            snap = _hour_snapshot(hourly, idx)
            index.setdefault(t_str, []).append(
                {
                    **snap,
                    "lat": float(pt["lat"]),
                    "lng": float(pt["lng"]),
                    "distance_km": float(pt.get("distance_km") or 0),
                    "elev_m": float(pt.get("elev_m") or 0),
                    "model_lat": float(fc.get("latitude") or pt["lat"]),
                    "model_lng": float(fc.get("longitude") or pt["lng"]),
                    "model_elev_m": (
                        float(fc["elevation"])
                        if fc.get("elevation") is not None
                        else None
                    ),
                    "sample_role": str(pt.get("sample_role") or "sector"),
                }
            )
    return index


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearest_sector_snap(
    sector_snaps: list[dict[str, Any]],
    lat: float,
    lng: float,
) -> dict[str, Any] | None:
    if not sector_snaps:
        return None
    return min(sector_snaps, key=lambda s: haversine_km(lat, lng, float(s["lat"]), float(s["lng"])))
