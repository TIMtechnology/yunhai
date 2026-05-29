from __future__ import annotations

import numpy as np

from app.adapters.dem import estimate_cloud_top_m
from app.adapters.open_meteo import estimate_cloud_base
from app.engine.cloudsea_scorer import _classify_cloudsea_archetype, _infer_effective_low_cloud

FEATURE_NAMES = [
    "cloud_low",
    "cloud_mid",
    "cloud_high",
    "visibility",
    "rh",
    "rh_850",
    "rh_700",
    "wind",
    "precip48",
    "hour",
    "month",
    "vis_km",
    "effective_low",
    "inversion",
    "is_type_a",
    "is_type_b",
    "is_fog_exclude",
]

DAY_FEATURE_NAMES = [
    "cloud_mid_mean",
    "cloud_mid_max",
    "cloud_low_mean",
    "cloud_high_mean",
    "cloud_high_max",
    "vis_min",
    "vis_mean",
    "rh_mean",
    "rh_max",
    "rh850_mean",
    "rh700_mean",
    "rh700_min",
    "wind_mean",
    "wind_max",
    "precip48",
    "month",
    "inversion_mean",
    "inversion_max",
    "hour_count_type_a",
    "hour_count_type_b",
    "hour_count_fog",
    "effective_low_mean",
]

TERRAIN_FEATURE_NAMES = [
    "elev_view_m",
    "elev_max_1km_m",
    "elev_max_5km_m",
    "relief_5km_m",
    "is_peak_overlook",
    "cloud_base_minus_peak_mean",
    "hours_above_cloud",
    "hours_valley_fill",
]

DAY_FEATURE_NAMES_V2 = list(DAY_FEATURE_NAMES)
DAY_FEATURE_NAMES = DAY_FEATURE_NAMES + TERRAIN_FEATURE_NAMES

_REQUIRED_METEO_KEYS = ("rh_700", "cloud_high", "inversion")


def label_to_target(status: str) -> float:
    if status in ("full", "partial"):
        return 1.0
    return 0.0


def _series_val(series: list, idx: int):
    if idx >= len(series):
        return None
    return series[idx]


def meteo_row_complete(row: dict) -> bool:
    return all(row.get(k) is not None for k in _REQUIRED_METEO_KEYS)


def build_meteo_hour_row(hourly: dict, idx: int, *, precip48: float | None = None) -> dict:
    times = hourly.get("time", [])
    t_str = times[idx]
    precips = hourly.get("precipitation", [])
    if precip48 is None:
        precip48 = sum(p or 0 for p in precips[max(0, idx - 48) : idx + 1])
    t_850 = _series_val(hourly.get("temperature_850hPa", []), idx)
    t_925 = _series_val(hourly.get("temperature_925hPa", []), idx)
    inversion = (float(t_850) - float(t_925)) if t_850 is not None and t_925 is not None else None
    return {
        "time": t_str,
        "precipitation": _series_val(hourly.get("precipitation", []), idx),
        "cloud_low": _series_val(hourly.get("cloud_cover_low", []), idx),
        "cloud_mid": _series_val(hourly.get("cloud_cover_mid", []), idx),
        "cloud_high": _series_val(hourly.get("cloud_cover_high", []), idx),
        "visibility": _series_val(hourly.get("visibility", []), idx),
        "rh": _series_val(hourly.get("relative_humidity_2m", []), idx),
        "rh_850": _series_val(hourly.get("relative_humidity_850hPa", []), idx),
        "rh_700": _series_val(hourly.get("relative_humidity_700hPa", []), idx),
        "t_850": t_850,
        "t_925": t_925,
        "inversion": inversion,
        "wind": _series_val(hourly.get("wind_speed_10m", []), idx),
        "precip48": precip48,
        "temp": _series_val(hourly.get("temperature_2m", []), idx),
        "dewpoint": _series_val(hourly.get("dew_point_2m", []), idx),
    }


def build_feature_row(
    raw: dict,
    *,
    elevation: float = 804.0,
) -> dict[str, float]:
    cloud_low = float(raw.get("cloud_low") or 0)
    cloud_mid = float(raw.get("cloud_mid") or 0)
    cloud_high = float(raw.get("cloud_high") or 0)
    visibility = raw.get("visibility")
    vis = float(visibility) if visibility is not None else 10000.0
    rh = float(raw.get("rh") or 70)
    rh_850 = raw.get("rh_850")
    rh_850_f = float(rh_850) if rh_850 is not None else 50.0
    rh_700 = raw.get("rh_700")
    rh_700_f = float(rh_700) if rh_700 is not None else 50.0
    wind = float(raw.get("wind") or 3)
    precip48 = float(raw.get("precip48") or 0)
    time_str = str(raw.get("time") or "2026-05-01T04:00")
    hour = int(time_str[11:13]) if "T" in time_str else 4
    month = int(time_str[5:7]) if len(time_str) >= 7 else 5
    inversion_raw = raw.get("inversion")
    if inversion_raw is None:
        t_850 = raw.get("t_850")
        t_925 = raw.get("t_925")
        if t_850 is not None and t_925 is not None:
            inversion_raw = float(t_850) - float(t_925)
    inversion = float(inversion_raw) if inversion_raw is not None else 0.0

    archetype, _ = _classify_cloudsea_archetype(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=vis if visibility is not None else None,
        rh=rh,
        rh_850=rh_850_f if rh_850 is not None else None,
        precip_recent=precip48,
        t_850=raw.get("t_850"),
        t_925=raw.get("t_925"),
    )
    effective_low, _ = _infer_effective_low_cloud(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=vis if visibility is not None else None,
        elevation=elevation,
        rh=rh,
        archetype=archetype,
    )

    return {
        "cloud_low": cloud_low,
        "cloud_mid": cloud_mid,
        "cloud_high": cloud_high,
        "visibility": vis,
        "rh": rh,
        "rh_850": rh_850_f,
        "rh_700": rh_700_f,
        "wind": wind,
        "precip48": precip48,
        "hour": float(hour),
        "month": float(month),
        "vis_km": vis / 1000.0,
        "effective_low": effective_low,
        "inversion": inversion,
        "is_type_a": 1.0 if archetype == "type_a" else 0.0,
        "is_type_b": 1.0 if archetype == "type_b" else 0.0,
        "is_fog_exclude": 1.0 if archetype == "fog_exclude" else 0.0,
    }


def feature_vector(raw: dict, *, elevation: float = 804.0) -> list[float]:
    row = build_feature_row(raw, elevation=elevation)
    return [row[name] for name in FEATURE_NAMES]


def _terrain_day_features(
    hour_rows: list[dict],
    *,
    elevation: float,
    terrain: dict | None,
) -> dict[str, float]:
    defaults = {n: 0.0 for n in TERRAIN_FEATURE_NAMES}
    if not terrain:
        return defaults

    elev_max_5km = float(terrain.get("elev_max_5km_m") or elevation)
    viewing_mode = str(terrain.get("viewing_mode") or "valley_fill")
    base_minus_peak: list[float] = []
    above_hours = 0
    valley_hours = 0

    for row in hour_rows:
        temp = row.get("temp")
        dew = row.get("dewpoint")
        if temp is None or dew is None:
            continue
        cloud_base = estimate_cloud_base(float(temp), float(dew))
        cloud_low = float(row.get("cloud_low") or 0)
        cloud_mid = float(row.get("cloud_mid") or 0)
        cloud_top = estimate_cloud_top_m(cloud_base, cloud_low, cloud_mid)
        base_minus_peak.append(cloud_base - elev_max_5km)
        if elevation > cloud_top:
            above_hours += 1
        if cloud_base < elev_max_5km and elevation >= elev_max_5km - 200:
            valley_hours += 1

    return {
        "elev_view_m": float(elevation),
        "elev_max_1km_m": float(terrain.get("elev_max_1km_m") or 0),
        "elev_max_5km_m": elev_max_5km,
        "relief_5km_m": float(terrain.get("relief_5km_m") or 0),
        "is_peak_overlook": 1.0 if viewing_mode == "peak_overlook" else 0.0,
        "cloud_base_minus_peak_mean": float(np.mean(base_minus_peak)) if base_minus_peak else 0.0,
        "hours_above_cloud": float(above_hours),
        "hours_valley_fill": float(valley_hours),
    }


def aggregate_day_features(
    hour_rows: list[dict],
    *,
    elevation: float = 804.0,
    terrain: dict | None = None,
) -> dict[str, float]:
    feats = [build_feature_row(r, elevation=elevation) for r in hour_rows]
    if not feats:
        return {n: 0.0 for n in DAY_FEATURE_NAMES}

    mids = [f["cloud_mid"] for f in feats]
    lows = [f["cloud_low"] for f in feats]
    highs = [f["cloud_high"] for f in feats]
    vis = [f["visibility"] for f in feats]
    rhs = [f["rh"] for f in feats]
    rh850s = [f["rh_850"] for f in feats]
    rh700s = [f["rh_700"] for f in feats]
    winds = [f["wind"] for f in feats]
    inversions = [f["inversion"] for f in feats]

    base = {
        "cloud_mid_mean": float(np.mean(mids)),
        "cloud_mid_max": float(np.max(mids)),
        "cloud_low_mean": float(np.mean(lows)),
        "cloud_high_mean": float(np.mean(highs)),
        "cloud_high_max": float(np.max(highs)),
        "vis_min": float(np.min(vis)),
        "vis_mean": float(np.mean(vis)),
        "rh_mean": float(np.mean(rhs)),
        "rh_max": float(np.max(rhs)),
        "rh850_mean": float(np.mean(rh850s)),
        "rh700_mean": float(np.mean(rh700s)),
        "rh700_min": float(np.min(rh700s)),
        "wind_mean": float(np.mean(winds)),
        "wind_max": float(np.max(winds)),
        "precip48": float(max(f["precip48"] for f in feats)),
        "month": float(feats[0]["month"]),
        "inversion_mean": float(np.mean(inversions)),
        "inversion_max": float(np.max(inversions)),
        "hour_count_type_a": float(sum(f["is_type_a"] for f in feats)),
        "hour_count_type_b": float(sum(f["is_type_b"] for f in feats)),
        "hour_count_fog": float(sum(f["is_fog_exclude"] for f in feats)),
        "effective_low_mean": float(np.mean([f["effective_low"] for f in feats])),
    }
    base.update(_terrain_day_features(hour_rows, elevation=elevation, terrain=terrain))
    return base


def hour_raw_from_forecast(
    *,
    t_str: str,
    idx: int,
    cloud_low: list,
    cloud_mid: list,
    cloud_high: list | None = None,
    visibilities: list,
    rhs: list,
    rh_850_series: list,
    rh_700_series: list | None = None,
    t_850_series: list | None = None,
    t_925_series: list | None = None,
    winds: list,
    precips: list,
    temps: list | None = None,
    dews: list | None = None,
) -> dict:
    precip48 = sum(p or 0 for p in precips[max(0, idx - 48) : idx + 1])
    t_850 = _series_val(t_850_series or [], idx)
    t_925 = _series_val(t_925_series or [], idx)
    inversion = (float(t_850) - float(t_925)) if t_850 is not None and t_925 is not None else None
    return {
        "time": t_str,
        "cloud_low": cloud_low[idx] if idx < len(cloud_low) else 0,
        "cloud_mid": cloud_mid[idx] if idx < len(cloud_mid) else 0,
        "cloud_high": _series_val(cloud_high or [], idx),
        "visibility": visibilities[idx] if idx < len(visibilities) else None,
        "rh": rhs[idx] if idx < len(rhs) else 70,
        "rh_850": rh_850_series[idx] if idx < len(rh_850_series) else None,
        "rh_700": _series_val(rh_700_series or [], idx),
        "t_850": t_850,
        "t_925": t_925,
        "inversion": inversion,
        "wind": winds[idx] if idx < len(winds) else 3,
        "precip48": precip48,
        "temp": _series_val(temps or [], idx),
        "dewpoint": _series_val(dews or [], idx),
    }
