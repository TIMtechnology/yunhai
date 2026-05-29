"""地形上下文（DEM v0）：基于 Copernicus GLO-90 网格采样。

Phase 0 实现：通过 Open-Meteo Elevation API 批量采样周边格点，
无需本地 GeoTIFF。与 terrain-cloudsea-plan Phase 1 指标对齐。
"""

from __future__ import annotations

import math
from typing import Any, Optional

from app.adapters.open_meteo import estimate_cloud_base, fetch_elevation, fetch_elevations_batch
from app.services.cache import cache_get, cache_set

EARTH_R_M = 6_371_000
VIEWING_MODES = ("valley_fill", "peak_overlook", "ridge_layer", "plateau_edge")


def _offset_latlng(lat: float, lng: float, dn_m: float, de_m: float) -> tuple[float, float]:
    dlat = dn_m / 111_320.0
    dlng = de_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat + dlat, lng + dlng


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def _grid_points(lat: float, lng: float, *, radius_km: float, spacing_km: float) -> list[tuple[float, float]]:
    radius_m = radius_km * 1000.0
    step_m = spacing_km * 1000.0
    n_steps = max(1, int(radius_m / step_m))
    points: list[tuple[float, float]] = []
    for i in range(-n_steps, n_steps + 1):
        for j in range(-n_steps, n_steps + 1):
            dn = i * step_m
            de = j * step_m
            if dn * dn + de * de <= radius_m * radius_m + 1:
                points.append(_offset_latlng(lat, lng, dn, de))
    return points


def _window_stats(elevations: list[float]) -> dict[str, float | int]:
    if not elevations:
        return {"max": 0.0, "min": 0.0, "relief": 0.0, "sample_count": 0}
    emax = max(elevations)
    emin = min(elevations)
    return {
        "max": round(emax, 1),
        "min": round(emin, 1),
        "relief": round(emax - emin, 1),
        "sample_count": len(elevations),
    }


def _slope_aspect(
    center_elev: float,
    north: float,
    south: float,
    east: float,
    west: float,
) -> tuple[float, float]:
    """近似坡度/坡向（度），采样间距约 90 m。"""
    dzdx = (east - west) / 180.0
    dzdy = (north - south) / 180.0
    slope = math.degrees(math.atan(math.sqrt(dzdx * dzdx + dzdy * dzdy)))
    aspect = math.degrees(math.atan2(-dzdx, dzdy))
    if aspect < 0:
        aspect += 360.0
    if center_elev <= 0 and north == south == east == west == center_elev:
        return 0.0, 0.0
    return round(slope, 1), round(aspect, 1)


def estimate_cloud_top_m(cloud_base_m: float, cloud_low_pct: float, cloud_mid_pct: float) -> float:
    low_thick = (cloud_low_pct / 100.0) * 450.0
    mid_thick = (cloud_mid_pct / 100.0) * 600.0
    return round(cloud_base_m + low_thick + mid_thick * 0.25, 1)


def infer_viewing_mode(
    *,
    elev_view: float,
    elev_max_5km: float,
    relief_5km: float,
) -> tuple[str, str]:
    """粗猜观云模式（viewing-mode-plan §5.2）。"""
    if elev_view >= 1800 and elev_view > elev_max_5km:
        return (
            "peak_overlook",
            "观景点高于 5 km 内峰顶，典型峰顶俯瞰型（如东灵山）",
        )
    if relief_5km >= 400 and elev_view < 1200:
        return (
            "valley_fill",
            "周边起伏大且观景点海拔适中，典型山谷填云型（如五女山、大黑山）",
        )
    if relief_5km >= 250 and abs(elev_view - elev_max_5km) <= 150:
        return (
            "ridge_layer",
            "观景点接近局部峰顶，可能为山脊层云型",
        )
    return (
        "valley_fill",
        "默认山谷填云型（与现网规则引擎一致）",
    )


def analyze_cloud_layer(
    *,
    viewer_m: float,
    cloud_base_m: float,
    cloud_top_m: float,
    elev_max_5km: float,
    elev_max_1km: float,
    visibility_m: float | None = None,
) -> dict[str, Any]:
    """云高 vs 地形：三态 + 山谷填云 / 雾型判据。"""
    above_cloud = viewer_m > cloud_top_m
    in_cloud = cloud_base_m < viewer_m < cloud_top_m
    below_cloud = viewer_m < cloud_base_m
    valley_fill = cloud_base_m < elev_max_5km and viewer_m >= elev_max_5km - 200
    under_fog = (
        cloud_base_m < 100
        and viewer_m <= elev_max_1km
        and visibility_m is not None
        and visibility_m < 2000
    )

    if above_cloud and valley_fill:
        layer = "above_cloudsea"
        label = "站在云海之上 · 俯瞰"
        note = f"云顶约 {cloud_top_m:.0f} m，低于观景点 {viewer_m:.0f} m，谷地可能有云海"
    elif in_cloud:
        layer = "in_cloud_layer"
        label = "人在云层中"
        note = f"云底 {cloud_base_m:.0f}–云顶 {cloud_top_m:.0f} m 之间，能见度主导体验"
    elif below_cloud:
        layer = "under_cloud"
        label = "观景点在云下"
        note = f"云底约 {cloud_base_m:.0f} m 高于或接近观景点，难以看到脚下云海"
    else:
        layer = "unclear"
        label = "层结关系不明确"
        note = "需结合低/中云量与能见度进一步判断"

    return {
        "viewer_elev_m": round(viewer_m, 1),
        "cloud_base_m": round(cloud_base_m, 1),
        "cloud_top_m": round(cloud_top_m, 1),
        "cloud_base_minus_peak_5km_m": round(cloud_base_m - elev_max_5km, 1),
        "viewer_minus_cloud_top_m": round(viewer_m - cloud_top_m, 1),
        "layer": layer,
        "layer_label": label,
        "layer_note": note,
        "valley_fill_potential": valley_fill,
        "fog_not_cloudsea": under_fog,
    }


def _dem_problems_summary(
    *,
    elev_view: float,
    open_meteo_elev: float,
    elev_max_1km: float,
    elev_max_5km: float,
    relief_5km: float,
    viewing_mode: str,
    cloud_layer: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """DEM 能回答的问题（供演示页展示）。"""
    items: list[dict[str, str]] = [
        {
            "id": "nearby_peak_1km",
            "title": "附近 1 km 最高海拔",
            "value": f"{elev_max_1km:.0f} m",
            "why": "莉景等产品用「附近峰顶 vs 云高」判断能否看到云海；单点 Open-Meteo 海拔无法给出。",
        },
        {
            "id": "valley_relief",
            "title": "5 km 地形起伏",
            "value": f"{relief_5km:.0f} m（峰 {elev_max_5km:.0f} m）",
            "why": "山谷云海需要足够起伏；平坦台地 vs 深谷评分逻辑不同。",
        },
        {
            "id": "viewing_mode",
            "title": "观云模式粗猜",
            "value": viewing_mode,
            "why": "五女山（山谷填云）与东灵山（峰顶俯瞰）不能用同一套 elevation_match 规则。",
        },
    ]
    delta = abs(elev_view - open_meteo_elev)
    if delta > 5:
        items.append(
            {
                "id": "elev_delta",
                "title": "DEM vs 手工海拔差",
                "value": f"{delta:.0f} m",
                "why": "校验 curated JSON 海拔；陡峭山脊处 90 m DEM 与人工标高可能偏差。",
            }
        )
    if cloud_layer:
        items.append(
            {
                "id": "cloud_layer",
                "title": "云–地相对位置",
                "value": cloud_layer["layer_label"],
                "why": "判断「人在云里 / 云上俯瞰 / 云下看天」——现网仅用单点海拔 + 云底公式，缺少周边峰顶参照。",
            }
        )
    return items


async def get_terrain_context(
    lat: float,
    lng: float,
    *,
    elevation: float | None = None,
    cloud_base_m: float | None = None,
    cloud_top_m: float | None = None,
    cloud_low_pct: float | None = None,
    cloud_mid_pct: float | None = None,
    temp_c: float | None = None,
    dewpoint_c: float | None = None,
    visibility_m: float | None = None,
) -> dict[str, Any]:
    cache_key = f"terrain:v0:{lat:.4f}:{lng:.4f}"
    cached = cache_get(cache_key)
    base: dict[str, Any] | None = dict(cached) if cached else None

    if base is None:
        pts_1km = _grid_points(lat, lng, radius_km=1.0, spacing_km=0.35)
        pts_5km = _grid_points(lat, lng, radius_km=5.0, spacing_km=1.2)
        # 坡向：东/西/南/北各 ~90 m
        cardinals = [
            _offset_latlng(lat, lng, 90, 0),
            _offset_latlng(lat, lng, -90, 0),
            _offset_latlng(lat, lng, 0, 90),
            _offset_latlng(lat, lng, 0, -90),
        ]
        all_pts = list(
            {
                (round(a, 5), round(b, 5))
                for a, b in ([(lat, lng)] + pts_1km + pts_5km + cardinals)
            }
        )
        lats = [p[0] for p in all_pts]
        lngs = [p[1] for p in all_pts]
        elevs = await fetch_elevations_batch(lats, lngs)
        pt_elev = dict(zip(all_pts, elevs))

        center_key = (round(lat, 5), round(lng, 5))
        elev_view = pt_elev[center_key]
        open_meteo_elev = elev_view

        elevs_1km = [
            e
            for (plat, plng), e in pt_elev.items()
            if _haversine_m(lat, lng, plat, plng) <= 1000
        ]
        elevs_5km = [
            e
            for (plat, plng), e in pt_elev.items()
            if _haversine_m(lat, lng, plat, plng) <= 5000
        ]
        w1 = _window_stats(elevs_1km)
        w5 = _window_stats(elevs_5km)

        def _pt_key(plat: float, plng: float) -> tuple[float, float]:
            return (round(plat, 5), round(plng, 5))

        n = pt_elev[_pt_key(*_offset_latlng(lat, lng, 90, 0))]
        s = pt_elev[_pt_key(*_offset_latlng(lat, lng, -90, 0))]
        e = pt_elev[_pt_key(*_offset_latlng(lat, lng, 0, 90))]
        w = pt_elev[_pt_key(*_offset_latlng(lat, lng, 0, -90))]
        slope_deg, aspect_deg = _slope_aspect(elev_view, n, s, e, w)

        base = {
            "lat": lat,
            "lng": lng,
            "source": "open_meteo_copernicus90",
            "dem_version": "v0_grid_sample",
            "elev_viewpoint_m": round(elev_view, 1),
            "elev_open_meteo_m": round(open_meteo_elev, 1),
            "elev_max_1km_m": w1["max"],
            "elev_min_1km_m": w1["min"],
            "elev_max_5km_m": w5["max"],
            "elev_min_5km_m": w5["min"],
            "relief_1km_m": w1["relief"],
            "relief_5km_m": w5["relief"],
            "slope_deg": slope_deg,
            "aspect_deg": aspect_deg,
            "sample_counts": {"radius_1km": w1["sample_count"], "radius_5km": w5["sample_count"]},
        }
        cache_set(cache_key, base, ttl=86400 * 7)

    result = dict(base)
    elev_for_mode = float(elevation if elevation is not None else result["elev_viewpoint_m"])
    mode, mode_note = infer_viewing_mode(
        elev_view=elev_for_mode,
        elev_max_5km=float(result["elev_max_5km_m"]),
        relief_5km=float(result["relief_5km_m"]),
    )
    result["viewing_mode"] = mode
    result["viewing_mode_note"] = mode_note
    result["viewing_mode_source"] = "auto_dem" if elevation is None else "auto_dem+curated_elev"
    if elevation is not None:
        result["elev_curated_m"] = elevation
        result["elev_curated_delta_m"] = round(elevation - float(result["elev_viewpoint_m"]), 1)

    resolved_base = cloud_base_m
    if resolved_base is None and temp_c is not None and dewpoint_c is not None:
        resolved_base = estimate_cloud_base(temp_c, dewpoint_c)
    resolved_top = cloud_top_m
    if resolved_base is not None and resolved_top is None and cloud_low_pct is not None:
        resolved_top = estimate_cloud_top_m(resolved_base, cloud_low_pct, cloud_mid_pct or 0.0)

    cloud_layer: dict[str, Any] | None = None
    if resolved_base is not None and resolved_top is not None:
        viewer = float(elevation if elevation is not None else result["elev_viewpoint_m"])
        cloud_layer = analyze_cloud_layer(
            viewer_m=viewer,
            cloud_base_m=resolved_base,
            cloud_top_m=resolved_top,
            elev_max_5km=float(result["elev_max_5km_m"]),
            elev_max_1km=float(result["elev_max_1km_m"]),
            visibility_m=visibility_m,
        )
        result["cloud_layer"] = cloud_layer

    result["problems_dem_solves"] = _dem_problems_summary(
        elev_view=float(result["elev_viewpoint_m"]),
        open_meteo_elev=float(result["elev_open_meteo_m"]),
        elev_max_1km=float(result["elev_max_1km_m"]),
        elev_max_5km=float(result["elev_max_5km_m"]),
        relief_5km=float(result["relief_5km_m"]),
        viewing_mode=str(result["viewing_mode"]),
        cloud_layer=cloud_layer,
    )
    return result


def get_terrain_context_sync(
    lat: float,
    lng: float,
    *,
    elevation: float | None = None,
) -> dict[str, Any]:
    """训练脚本等同步环境使用。"""
    import asyncio

    return asyncio.run(get_terrain_context(lat, lng, elevation=elevation))
