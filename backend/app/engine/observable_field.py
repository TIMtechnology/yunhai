"""可观测场：观云模式 × DEM × 气象 → 哪些地面区域可形成并被看到的云海。"""

from __future__ import annotations

from typing import Any

from app.adapters.open_meteo import estimate_cloud_base
from app.adapters.dem import estimate_cloud_top_m
from app.adapters.sector_meteo import nearest_sector_snap
from app.engine.utils import bell_score, clamp, range_score

DEFAULT_SECTOR_HALF_DEG = 45.0
DEFAULT_MAX_RANGE_KM = 30.0


def _angular_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _visible_range_km(
    visibility_m: float | None,
    *,
    max_range_km: float = DEFAULT_MAX_RANGE_KM,
    viewing_mode: str = "valley_fill",
    summit_cloud_low: float = 0.0,
    summit_rh: float = 70.0,
) -> float:
    if visibility_m is None or visibility_m <= 0:
        return max_range_km
    vis_km = min(visibility_m / 1000.0, max_range_km)
    # 峰顶厚低云日 NWP 能见度常偏短，扇区几何仍应看到数 km 外谷地
    if (
        viewing_mode == "peak_overlook"
        and summit_cloud_low >= 70
        and summit_rh >= 88
        and vis_km < 5.0
    ):
        return max(vis_km, 5.0)
    return vis_km


def _moisture_factor(rh_850: float | None, rh_700: float | None) -> float:
    score = 0.35
    if rh_850 is not None:
        score = max(score, range_score(rh_850, 58, 92) * 0.85)
    if rh_700 is not None:
        score = max(score, range_score(rh_700, 50, 85) * 0.55)
    return clamp(score)


def _local_cloud_signal(cloud_low: float, rh: float, rh_850: float | None) -> bool:
    if cloud_low >= 10:
        return True
    if rh >= 82:
        return True
    if rh_850 is not None and rh_850 >= 68 and cloud_low >= 5:
        return True
    return False


def _sector_point_cloud_signal(cloud_low: float, rh: float, rh_850: float | None) -> bool:
    """扇区网格：避免仅凭 valley 高湿把几何可填点全部算作有云。"""
    if cloud_low >= 15:
        return True
    if cloud_low >= 10 and rh >= 70:
        return True
    if cloud_low >= 8 and rh_850 is not None and rh_850 >= 65:
        return True
    return False


def _point_fillable_with_meteo(
    *,
    ground_m: float,
    viewer_m: float,
    cloud_base_m: float,
    cloud_top_m: float,
    cloud_low: float,
    rh: float,
    rh_850: float | None,
    sector_point: bool = False,
) -> bool:
    if ground_m >= viewer_m - 30:
        return False
    if cloud_base_m >= ground_m:
        return False
    signal_fn = _sector_point_cloud_signal if sector_point else _local_cloud_signal
    if not signal_fn(cloud_low, rh, rh_850):
        return False
    return viewer_m > cloud_top_m - 120


def compute_observable_field(
    *,
    viewer_elev_m: float,
    cloud_base_m: float,
    cloud_top_m: float,
    visibility_m: float | None,
    elev_profile_sunrise: list[dict[str, Any]] | None,
    viewing_mode: str = "valley_fill",
    rh_850: float | None = None,
    rh_700: float | None = None,
    sunrise_azimuth_deg: float | None = None,
    sector_half_deg: float = DEFAULT_SECTOR_HALF_DEG,
    max_range_km: float = DEFAULT_MAX_RANGE_KM,
    elev_max_5km_m: float | None = None,
    sector_meteo: list[dict[str, Any]] | None = None,
    summit_cloud_low: float = 0.0,
    summit_rh: float = 70.0,
) -> dict[str, Any]:
    """计算当前时刻可观测云海场摘要。"""
    vis_range = _visible_range_km(
        visibility_m,
        max_range_km=max_range_km,
        viewing_mode=viewing_mode,
        summit_cloud_low=summit_cloud_low,
        summit_rh=summit_rh,
    )
    moisture = _moisture_factor(rh_850, rh_700)

    if viewing_mode != "peak_overlook" or not elev_profile_sunrise:
        return _valley_fill_field(
            viewer_elev_m=viewer_elev_m,
            cloud_base_m=cloud_base_m,
            cloud_top_m=cloud_top_m,
            visibility_m=visibility_m,
            elev_max_5km_m=elev_max_5km_m or viewer_elev_m,
            moisture=moisture,
            vis_range_km=vis_range,
        )

    az_center = sunrise_azimuth_deg if sunrise_azimuth_deg is not None else 90.0
    eligible: list[dict[str, Any]] = []
    fillable: list[dict[str, Any]] = []
    geometry_fillable: list[dict[str, Any]] = []
    sector_elevs: list[float] = []
    sector_cloud_lows: list[float] = []
    sector_rhs: list[float] = []

    for pt in elev_profile_sunrise:
        dist = float(pt.get("distance_km") or 0)
        if dist <= 0 or dist > vis_range:
            continue
        pt_az = float(pt.get("azimuth_deg") or az_center)
        if _angular_diff(pt_az, az_center) > sector_half_deg:
            continue
        ground = float(pt.get("elev_m") or 0)
        plat = float(pt.get("lat") or 0)
        plng = float(pt.get("lng") or 0)
        sector_elevs.append(ground)
        if ground >= viewer_elev_m - 30:
            continue
        eligible.append(pt)

        if not sector_meteo:
            if cloud_base_m < ground < viewer_elev_m - 20:
                fillable.append({**pt, "ground_elev_m": ground, "depth_m": ground - cloud_top_m})
            continue

        snap = nearest_sector_snap(sector_meteo, plat, plng)
        local_base = cloud_base_m
        local_top = cloud_top_m
        local_low = summit_cloud_low
        local_rh = summit_rh
        if snap and snap.get("cloud_base_m") is not None and snap.get("cloud_top_m") is not None:
            local_base = float(snap["cloud_base_m"])
            local_top = float(snap["cloud_top_m"])
            local_low = float(snap.get("cloud_low") or 0)
            local_rh = float(snap.get("rh") or 70)
            sector_cloud_lows.append(local_low)
            sector_rhs.append(local_rh)
        elif cloud_base_m < ground < viewer_elev_m - 20:
            geometry_fillable.append(pt)

        if _point_fillable_with_meteo(
            ground_m=ground,
            viewer_m=viewer_elev_m,
            cloud_base_m=local_base,
            cloud_top_m=local_top,
            cloud_low=local_low,
            rh=local_rh,
            rh_850=rh_850,
            sector_point=bool(snap),
        ):
            fillable.append(
                {
                    **pt,
                    "ground_elev_m": ground,
                    "depth_m": ground - local_top,
                    "local_cloud_low": local_low,
                }
            )

    n_eligible = max(len(eligible), 1)
    raw_fraction = len(fillable) / n_eligible if eligible else 0.0
    geometry_fraction = (
        len(geometry_fillable) / n_eligible if eligible and geometry_fillable else raw_fraction
    )

    sector_cloud_mean = float(sum(sector_cloud_lows) / len(sector_cloud_lows)) if sector_cloud_lows else None
    sector_rh_mean = float(sum(sector_rhs) / len(sector_rhs)) if sector_rhs else None

    # 扇区多点：几何可填但各点无云信号 → 降权（减少 Aug 误报）
    if sector_meteo and sector_cloud_mean is not None:
        if sector_cloud_mean < 8 and (sector_rh_mean or 0) < 78:
            raw_fraction = min(raw_fraction, geometry_fraction * 0.25)
        elif sector_cloud_mean >= 20:
            raw_fraction = max(raw_fraction, min(1.0, geometry_fraction * 0.6 + sector_cloud_mean / 100.0 * 0.4))

    observable_fraction = clamp(raw_fraction * (0.55 + 0.45 * moisture))

    if sector_meteo and sector_cloud_mean is not None and sector_cloud_mean < 8 and (sector_rh_mean or 0) < 78:
        observable_fraction = min(observable_fraction, 0.10 + sector_cloud_mean / 100.0 * 0.08)

    depths = [p["depth_m"] for p in fillable if p["depth_m"] > 0]
    observable_depth = float(sum(depths) / len(depths)) if depths else 0.0

    viewer_above = viewer_elev_m > cloud_top_m
    viewer_below_base = viewer_elev_m < cloud_base_m
    horizon_blocked = False

    summit_in_cloud = summit_cloud_low >= 70 and summit_rh >= 88
    if viewer_below_base and summit_in_cloud and viewing_mode == "peak_overlook":
        # 峰顶报满低云+饱和湿度：人在云系内/云上，不应按「云下」压到 0
        viewer_below_base = False
        observable_fraction = max(
            observable_fraction,
            0.32 + min(summit_cloud_low, 100) / 100.0 * 0.25,
        )
    elif viewer_below_base:
        observable_fraction = min(observable_fraction, 0.08)
    elif viewer_above and observable_fraction >= 0.2:
        sector_dry = (
            sector_meteo
            and sector_cloud_mean is not None
            and sector_cloud_mean < 8
            and (sector_rh_mean or 0) < 78
        )
        if not sector_dry:
            observable_fraction = max(
                observable_fraction,
                0.28 + bell_score(observable_depth, 300, 400) * 0.35,
            )

    sector_relief = (max(sector_elevs) - min(sector_elevs)) if sector_elevs else 0.0
    valley_elev = min(sector_elevs) if sector_elevs else (elev_max_5km_m or viewer_elev_m)

    note_parts: list[str] = [
        f"日出扇区±{sector_half_deg:.0f}°·可见{vis_range:.0f}km",
        f"可填云 {len(fillable)}/{len(eligible)} 点 ({observable_fraction:.0%})",
    ]
    if sector_meteo:
        note_parts.append(
            f"扇区低云均{sector_cloud_mean:.0f}%"
            if sector_cloud_mean is not None
            else "扇区多点气象"
        )
    if viewer_above:
        note_parts.append("人在云上")
    elif viewer_elev_m > cloud_base_m:
        note_parts.append("云缘")
    else:
        note_parts.append("人在云下")

    return {
        "viewing_mode": viewing_mode,
        "observable_fraction": round(observable_fraction, 3),
        "observable_depth_m": round(observable_depth, 1),
        "visible_range_km": round(vis_range, 1),
        "sunrise_azimuth_deg": round(az_center, 1),
        "sector_half_deg": sector_half_deg,
        "eligible_points": len(eligible),
        "fillable_points": len(fillable),
        "sunrise_sector_relief_m": round(sector_relief, 1),
        "sunrise_sector_valley_m": round(float(valley_elev), 1),
        "cloud_base_minus_valley_m": round(cloud_base_m - float(valley_elev), 1),
        "horizon_blocked": horizon_blocked,
        "viewer_above_cloud": viewer_above,
        "viewer_below_cloud_base": viewer_below_base,
        "moisture_factor": round(moisture, 3),
        "sector_cloud_low_mean": round(sector_cloud_mean, 1) if sector_cloud_mean is not None else None,
        "sector_rh_mean": round(sector_rh_mean, 1) if sector_rh_mean is not None else None,
        "sector_meteo_points": len(sector_meteo or []),
        "geometry_fillable_fraction": round(geometry_fraction, 3) if eligible else 0.0,
        "note": " · ".join(note_parts),
    }


def _valley_fill_field(
    *,
    viewer_elev_m: float,
    cloud_base_m: float,
    cloud_top_m: float,
    visibility_m: float | None,
    elev_max_5km_m: float,
    moisture: float,
    vis_range_km: float,
) -> dict[str, Any]:
    in_layer = cloud_base_m < viewer_elev_m < cloud_base_m + 800
    valley_fill = cloud_base_m < elev_max_5km_m and viewer_elev_m >= elev_max_5km_m - 200
    fraction = 0.0
    if in_layer:
        fraction = 0.72
    elif valley_fill and cloud_base_m < elev_max_5km_m:
        gap = max(elev_max_5km_m - cloud_base_m, 0.0)
        fraction = clamp(0.35 + bell_score(gap, 250, 350) * 0.45)
    fraction *= 0.6 + 0.4 * moisture

    return {
        "viewing_mode": "valley_fill",
        "observable_fraction": round(fraction, 3),
        "observable_depth_m": round(max(elev_max_5km_m - cloud_top_m, 0.0), 1),
        "visible_range_km": round(vis_range_km, 1),
        "sunrise_azimuth_deg": None,
        "sector_half_deg": None,
        "eligible_points": 1,
        "fillable_points": 1 if fraction >= 0.35 else 0,
        "sunrise_sector_relief_m": None,
        "sunrise_sector_valley_m": round(elev_max_5km_m, 1),
        "cloud_base_minus_valley_m": round(cloud_base_m - elev_max_5km_m, 1),
        "horizon_blocked": False,
        "viewer_above_cloud": viewer_elev_m > cloud_top_m,
        "viewer_below_cloud_base": viewer_elev_m < cloud_base_m,
        "moisture_factor": round(moisture, 3),
        "note": "山谷填云：观景点与云底/谷地匹配",
    }


def score_observable_field(obs: dict[str, Any]) -> tuple[float, str]:
    """将可观测场摘要转为 0–1 评分。"""
    frac = float(obs.get("observable_fraction") or 0.0)
    depth = float(obs.get("observable_depth_m") or 0.0)
    moisture = float(obs.get("moisture_factor") or 0.5)

    if obs.get("viewer_below_cloud_base"):
        return 0.10, str(obs.get("note") or "观景点在云下")

    score = clamp(0.25 + frac * 0.55 + bell_score(depth, 350, 450) * 0.15)
    score = clamp(score * (0.7 + 0.3 * moisture))

    if obs.get("viewing_mode") == "peak_overlook" and obs.get("viewer_above_cloud") and frac >= 0.35:
        score = max(score, 0.68)

    return score, str(obs.get("note") or "")


def observable_cloudsea_evidence(obs: dict[str, Any], *, threshold: float = 0.28) -> bool:
    return float(obs.get("observable_fraction") or 0.0) >= threshold and not obs.get(
        "viewer_below_cloud_base"
    )
