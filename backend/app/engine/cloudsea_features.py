from __future__ import annotations

import numpy as np
from datetime import date

from app.adapters.dem import estimate_cloud_top_m
from app.adapters.open_meteo import estimate_cloud_base
from app.engine.cloudsea_scorer import _classify_cloudsea_archetype, _infer_effective_low_cloud
from app.engine.observable_field import compute_observable_field

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
    # v5：低 vis 按 rh 分层，避免 vis 单独压分；加强 fog 区分力
    "hour_count_dry_low_vis",
    "hour_count_dry_low_vis_boost",
    "hour_count_wet_low_vis",
    "day_dry_low_vis_flag",
    "hour_count_fog_boost",
]

VIS_LOW_THRESHOLD_M = 500.0
RH_DRY_THRESHOLD = 75.0
RH_WET_THRESHOLD = 85.0
FOG_BOOST_FACTOR = 2.0
DRY_LOW_VIS_BOOST_FACTOR = 2.0
MIST_DISCRIM_FEATURE_NAMES = [
    "hour_count_dry_low_vis",
    "hour_count_dry_low_vis_boost",
    "hour_count_wet_low_vis",
    "day_dry_low_vis_flag",
    "hour_count_fog_boost",
]

# 三元组诊断（5/29·5/25·5/28）重点特征
TRIPLET_DISCRIM_FEATURE_NAMES = [
    "vis_min",
    "rh_mean",
    "rh850_mean",
    "hour_count_fog",
    "hour_count_fog_boost",
    "hour_count_dry_low_vis",
    "hour_count_dry_low_vis_boost",
    "hour_count_wet_low_vis",
    "day_dry_low_vis_flag",
    "effective_low_mean",
    "observable_depth_mean",
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

OBSERVABLE_FEATURE_NAMES = [
    "observable_fraction_mean",
    "observable_fraction_max",
    "observable_depth_mean",
    "sunrise_sector_relief_m",
    "horizon_blocked",
    "vis_limited_range_km_mean",
    "hours_observable_gt_03",
    "cloud_base_minus_valley_mean",
]

DAY_FEATURE_NAMES_V2 = list(DAY_FEATURE_NAMES)
DAY_FEATURE_NAMES_V3 = DAY_FEATURE_NAMES + TERRAIN_FEATURE_NAMES
DAY_FEATURE_NAMES = DAY_FEATURE_NAMES + TERRAIN_FEATURE_NAMES + OBSERVABLE_FEATURE_NAMES

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

    vis_for_thresh = vis if visibility is not None else 10000.0
    is_fog = archetype == "fog_exclude"
    is_dry_low_vis = (
        1.0
        if vis_for_thresh <= VIS_LOW_THRESHOLD_M and rh < RH_DRY_THRESHOLD and not is_fog
        else 0.0
    )
    is_wet_low_vis = (
        1.0
        if vis_for_thresh <= VIS_LOW_THRESHOLD_M and (is_fog or rh >= RH_WET_THRESHOLD)
        else 0.0
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
        "is_fog_exclude": 1.0 if is_fog else 0.0,
        "is_dry_low_vis": is_dry_low_vis,
        "is_wet_low_vis": is_wet_low_vis,
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


def _observable_day_features(
    hour_rows: list[dict],
    *,
    elevation: float,
    terrain: dict | None,
    viewing_mode: str,
) -> dict[str, float]:
    defaults = {n: 0.0 for n in OBSERVABLE_FEATURE_NAMES}
    if not hour_rows:
        return defaults

    fractions: list[float] = []
    depths: list[float] = []
    vis_ranges: list[float] = []
    valley_gaps: list[float] = []
    hours_gt = 0.0
    horizon = 0.0
    sector_relief = float((terrain or {}).get("sunrise_sector_relief_m") or 0.0)

    for row in hour_rows:
        temp = row.get("temp")
        dew = row.get("dewpoint")
        if temp is None or dew is None:
            continue
        cloud_base = estimate_cloud_base(float(temp), float(dew))
        cloud_low = float(row.get("cloud_low") or 0)
        cloud_mid = float(row.get("cloud_mid") or 0)
        cloud_top = estimate_cloud_top_m(cloud_base, cloud_low, cloud_mid)
        vis = row.get("visibility")
        vis_m = float(vis) if vis is not None else None
        obs = compute_observable_field(
            viewer_elev_m=elevation,
            cloud_base_m=cloud_base,
            cloud_top_m=cloud_top,
            visibility_m=vis_m,
            elev_profile_sunrise=(terrain or {}).get("elev_profile_sunrise"),
            viewing_mode=viewing_mode,
            rh_850=row.get("rh_850"),
            rh_700=row.get("rh_700"),
            sunrise_azimuth_deg=(terrain or {}).get("sunrise_azimuth_deg"),
            elev_max_5km_m=float((terrain or {}).get("elev_max_5km_m") or elevation),
        )
        frac = float(obs.get("observable_fraction") or 0.0)
        fractions.append(frac)
        depths.append(float(obs.get("observable_depth_m") or 0.0))
        vis_ranges.append(float(obs.get("visible_range_km") or 0.0))
        valley_gaps.append(float(obs.get("cloud_base_minus_valley_m") or 0.0))
        if frac >= 0.3:
            hours_gt += 1.0
        if obs.get("horizon_blocked"):
            horizon = 1.0
        if obs.get("sunrise_sector_relief_m") is not None:
            sector_relief = float(obs["sunrise_sector_relief_m"])

    if not fractions:
        return defaults

    return {
        "observable_fraction_mean": float(np.mean(fractions)),
        "observable_fraction_max": float(np.max(fractions)),
        "observable_depth_mean": float(np.mean(depths)),
        "sunrise_sector_relief_m": sector_relief,
        "horizon_blocked": horizon,
        "vis_limited_range_km_mean": float(np.mean(vis_ranges)),
        "hours_observable_gt_03": hours_gt,
        "cloud_base_minus_valley_mean": float(np.mean(valley_gaps)),
    }


def aggregate_day_features(
    hour_rows: list[dict],
    *,
    elevation: float = 804.0,
    terrain: dict | None = None,
    use_observable_field: bool = True,
    use_mist_discrim_features: bool = True,
) -> dict[str, float]:
    feats = [build_feature_row(r, elevation=elevation) for r in hour_rows]
    names_out = (
        DAY_FEATURE_NAMES
        if use_mist_discrim_features
        else [n for n in DAY_FEATURE_NAMES if n not in MIST_DISCRIM_FEATURE_NAMES]
    )
    if not feats:
        return {n: 0.0 for n in names_out}

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
    fog_count = int(base["hour_count_fog"])
    if use_mist_discrim_features:
        dry_count = float(sum(f["is_dry_low_vis"] for f in feats))
        base["hour_count_dry_low_vis"] = dry_count
        base["hour_count_dry_low_vis_boost"] = dry_count * DRY_LOW_VIS_BOOST_FACTOR
        base["hour_count_wet_low_vis"] = float(sum(f["is_wet_low_vis"] for f in feats))
        base["day_dry_low_vis_flag"] = (
            DRY_LOW_VIS_BOOST_FACTOR
            if base["vis_min"] <= VIS_LOW_THRESHOLD_M
            and base["rh_mean"] < RH_DRY_THRESHOLD
            and fog_count == 0
            else 0.0
        )
        base["hour_count_fog_boost"] = float(fog_count * FOG_BOOST_FACTOR)
    base.update(_terrain_day_features(hour_rows, elevation=elevation, terrain=terrain))
    if use_observable_field:
        viewing_mode = str((terrain or {}).get("viewing_mode") or "valley_fill")
        base.update(
            _observable_day_features(
                hour_rows,
                elevation=elevation,
                terrain=terrain,
                viewing_mode=viewing_mode,
            )
        )
    else:
        for name in OBSERVABLE_FEATURE_NAMES:
            base[name] = 0.0
    if not use_mist_discrim_features:
        for name in MIST_DISCRIM_FEATURE_NAMES:
            base.pop(name, None)
    return {n: float(base.get(n, 0.0)) for n in names_out}


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


PRECURSOR_SEGMENT_NAMES = ("evening", "night", "dawn")

# v7 增量：evening + night + 跨段趋势 + 季节；不含 dawn（由 DAY_FEATURE_NAMES 覆盖）
V7_INCREMENTAL_NAMES = [
    "evening_rh_mean",
    "evening_rh_max",
    "evening_cloud_low_mean",
    "evening_cloud_low_max",
    "evening_wind_mean",
    "evening_inversion_mean",
    "night_rh_mean",
    "night_rh_max",
    "night_cloud_low_mean",
    "night_cloud_mid_mean",
    "night_wind_mean",
    "night_inversion_mean",
    "delta_rh_night_to_dawn",
    "delta_cloud_low_evening_to_dawn",
    "delta_wind_night_to_dawn",
    "rh_monotonic_night",
    "dawn_vs_night_type_b_delta",
    "doy_sin",
    "doy_cos",
]

V7_FEATURE_NAMES = list(DAY_FEATURE_NAMES) + V7_INCREMENTAL_NAMES

PRECURSOR_FEATURE_NAMES = [
    "evening_rh_mean",
    "evening_rh_max",
    "evening_cloud_low_mean",
    "evening_cloud_low_max",
    "evening_wind_mean",
    "evening_inversion_mean",
    "night_rh_mean",
    "night_rh_max",
    "night_cloud_low_mean",
    "night_cloud_mid_mean",
    "night_wind_mean",
    "night_inversion_mean",
    "dawn_rh_mean",
    "dawn_rh_max",
    "dawn_cloud_low_mean",
    "dawn_cloud_low_max",
    "dawn_wind_mean",
    "dawn_vis_min",
    "dawn_inversion_mean",
    "delta_rh_night_to_dawn",
    "delta_cloud_low_evening_to_dawn",
    "delta_wind_night_to_dawn",
    "rh_monotonic_night",
    "dawn_vs_night_type_b_delta",
    "doy_sin",
    "doy_cos",
    "precip48",
]


def _hour_from_row(row: dict) -> int:
    time_str = str(row.get("time") or "")
    return int(time_str[11:13]) if "T" in time_str else 0


def _date_from_row(row: dict) -> str:
    time_str = str(row.get("time") or "")
    return time_str[:10] if len(time_str) >= 10 else ""


def _segment_for_row(row: dict, target_date: str) -> str | None:
    hour = _hour_from_row(row)
    row_date = _date_from_row(row)
    if row_date < target_date and hour >= 20:
        return "evening"
    if row_date == target_date and hour < 3:
        return "night"
    if row_date == target_date and 3 <= hour < 8:
        return "dawn"
    return None


def _segment_aggregate(rows: list[dict], *, elevation: float) -> dict[str, float]:
    if not rows:
        return {}
    feats = [build_feature_row(r, elevation=elevation) for r in rows]
    return {
        "rh_mean": float(np.mean([f["rh"] for f in feats])),
        "rh_max": float(np.max([f["rh"] for f in feats])),
        "cloud_low_mean": float(np.mean([f["cloud_low"] for f in feats])),
        "cloud_low_max": float(np.max([f["cloud_low"] for f in feats])),
        "cloud_mid_mean": float(np.mean([f["cloud_mid"] for f in feats])),
        "wind_mean": float(np.mean([f["wind"] for f in feats])),
        "vis_min": float(np.min([f["visibility"] for f in feats])),
        "inversion_mean": float(np.mean([f["inversion"] for f in feats])),
        "type_b_count": float(sum(f["is_type_b"] for f in feats)),
    }


def filter_dawn_rows(hour_rows: list[dict], target_date: str) -> list[dict]:
    """标注日 D 的 dawn 段（03–07），供 v7 与 v6 一致的日出窗聚合。"""
    out = [r for r in hour_rows if _segment_for_row(r, target_date) == "dawn"]
    return sorted(out, key=lambda r: str(r.get("time")))


def _build_v7_incremental_features(
    segments: dict[str, list[dict]],
    *,
    target_date: str,
    hour_rows: list[dict],
    elevation: float,
) -> dict[str, float]:
    evening = _segment_aggregate(segments["evening"], elevation=elevation)
    night = _segment_aggregate(segments["night"], elevation=elevation)
    dawn = _segment_aggregate(segments["dawn"], elevation=elevation)

    night_sorted = sorted(segments["night"], key=lambda r: str(r.get("time")))
    rh_mono = 0.0
    if len(night_sorted) >= 2:
        rhs = [build_feature_row(r, elevation=elevation)["rh"] for r in night_sorted]
        rh_mono = 1.0 if all(rhs[i] <= rhs[i + 1] for i in range(len(rhs) - 1)) else 0.0

    d = date.fromisoformat(target_date) if target_date else date(2026, 5, 1)
    doy = float(d.timetuple().tm_yday)

    out = {
        "evening_rh_mean": evening.get("rh_mean", 0.0),
        "evening_rh_max": evening.get("rh_max", 0.0),
        "evening_cloud_low_mean": evening.get("cloud_low_mean", 0.0),
        "evening_cloud_low_max": evening.get("cloud_low_max", 0.0),
        "evening_wind_mean": evening.get("wind_mean", 0.0),
        "evening_inversion_mean": evening.get("inversion_mean", 0.0),
        "night_rh_mean": night.get("rh_mean", 0.0),
        "night_rh_max": night.get("rh_max", 0.0),
        "night_cloud_low_mean": night.get("cloud_low_mean", 0.0),
        "night_cloud_mid_mean": night.get("cloud_mid_mean", 0.0),
        "night_wind_mean": night.get("wind_mean", 0.0),
        "night_inversion_mean": night.get("inversion_mean", 0.0),
        "delta_rh_night_to_dawn": dawn.get("rh_mean", 0.0) - night.get("rh_mean", 0.0),
        "delta_cloud_low_evening_to_dawn": dawn.get("cloud_low_mean", 0.0) - evening.get("cloud_low_mean", 0.0),
        "delta_wind_night_to_dawn": dawn.get("wind_mean", 0.0) - night.get("wind_mean", 0.0),
        "rh_monotonic_night": rh_mono,
        "dawn_vs_night_type_b_delta": dawn.get("type_b_count", 0.0) - night.get("type_b_count", 0.0),
        "doy_sin": float(np.sin(2 * np.pi * doy / 365.25)),
        "doy_cos": float(np.cos(2 * np.pi * doy / 365.25)),
    }
    return {n: float(out.get(n, 0.0)) for n in V7_INCREMENTAL_NAMES}


def aggregate_v7_features(
    hour_rows: list[dict],
    *,
    target_date: str,
    elevation: float = 804.0,
    terrain: dict | None = None,
    use_observable_field: bool = True,
    use_mist_discrim_features: bool = True,
) -> dict[str, float]:
    """v7：v6 dawn 全量特征 + evening/night/趋势增量（train/serve 均用 precursor 窗）。"""
    segments: dict[str, list[dict]] = {name: [] for name in PRECURSOR_SEGMENT_NAMES}
    for row in hour_rows:
        seg = _segment_for_row(row, target_date)
        if seg:
            segments[seg].append(row)

    dawn_feat = aggregate_day_features(
        segments["dawn"],
        elevation=elevation,
        terrain=terrain,
        use_observable_field=use_observable_field,
        use_mist_discrim_features=use_mist_discrim_features,
    )
    incr_feat = _build_v7_incremental_features(
        segments,
        target_date=target_date,
        hour_rows=hour_rows,
        elevation=elevation,
    )
    merged = {**dawn_feat, **incr_feat}
    return {n: float(merged.get(n, 0.0)) for n in V7_FEATURE_NAMES}


def aggregate_precursor_features(
    hour_rows: list[dict],
    *,
    target_date: str,
    elevation: float = 804.0,
) -> dict[str, float]:
    """前夜–清晨过程特征（evening / night / dawn + 跨段趋势）。"""
    segments: dict[str, list[dict]] = {name: [] for name in PRECURSOR_SEGMENT_NAMES}
    for row in hour_rows:
        seg = _segment_for_row(row, target_date)
        if seg:
            segments[seg].append(row)

    evening = _segment_aggregate(segments["evening"], elevation=elevation)
    night = _segment_aggregate(segments["night"], elevation=elevation)
    dawn = _segment_aggregate(segments["dawn"], elevation=elevation)

    incr = _build_v7_incremental_features(
        segments,
        target_date=target_date,
        hour_rows=hour_rows,
        elevation=elevation,
    )
    precip48 = float(max((build_feature_row(r, elevation=elevation)["precip48"] for r in hour_rows), default=0.0))

    out = {
        **{f"dawn_{k}": v for k, v in {
            "rh_mean": dawn.get("rh_mean", 0.0),
            "rh_max": dawn.get("rh_max", 0.0),
            "cloud_low_mean": dawn.get("cloud_low_mean", 0.0),
            "cloud_low_max": dawn.get("cloud_low_max", 0.0),
            "wind_mean": dawn.get("wind_mean", 0.0),
            "vis_min": dawn.get("vis_min", 0.0),
            "inversion_mean": dawn.get("inversion_mean", 0.0),
        }.items()},
        **incr,
        "precip48": precip48,
    }
    # legacy precursor-only names (dawn_* flat keys)
    out.update({
        "evening_rh_mean": incr["evening_rh_mean"],
        "evening_rh_max": incr["evening_rh_max"],
        "evening_cloud_low_mean": incr["evening_cloud_low_mean"],
        "evening_cloud_low_max": incr["evening_cloud_low_max"],
        "evening_wind_mean": incr["evening_wind_mean"],
        "evening_inversion_mean": incr["evening_inversion_mean"],
        "night_rh_mean": incr["night_rh_mean"],
        "night_rh_max": incr["night_rh_max"],
        "night_cloud_low_mean": incr["night_cloud_low_mean"],
        "night_cloud_mid_mean": incr["night_cloud_mid_mean"],
        "night_wind_mean": incr["night_wind_mean"],
        "night_inversion_mean": incr["night_inversion_mean"],
        "dawn_rh_mean": out["dawn_rh_mean"],
        "dawn_rh_max": out["dawn_rh_max"],
        "dawn_cloud_low_mean": out["dawn_cloud_low_mean"],
        "dawn_cloud_low_max": out["dawn_cloud_low_max"],
        "dawn_wind_mean": out["dawn_wind_mean"],
        "dawn_vis_min": out["dawn_vis_min"],
        "dawn_inversion_mean": out["dawn_inversion_mean"],
        "delta_rh_night_to_dawn": incr["delta_rh_night_to_dawn"],
        "delta_cloud_low_evening_to_dawn": incr["delta_cloud_low_evening_to_dawn"],
        "delta_wind_night_to_dawn": incr["delta_wind_night_to_dawn"],
        "rh_monotonic_night": incr["rh_monotonic_night"],
        "dawn_vs_night_type_b_delta": incr["dawn_vs_night_type_b_delta"],
        "doy_sin": incr["doy_sin"],
        "doy_cos": incr["doy_cos"],
    })
    return {n: float(out.get(n, 0.0)) for n in PRECURSOR_FEATURE_NAMES}
