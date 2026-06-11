"""局地大型水体（水库/湖泊）对清晨谷地雾 / 填谷云海的物理潜势估计。

动机：seamless（~11km）网格气象无法分辨山脚水库的蒸发与谷地冷池效应。
五女山点将台脚下即桓龙湖（~88km² 水面、落差约 500m），晴朗弱风的清晨，
水面蒸发 + 谷地辐射冷却极易形成填谷型平流/辐射雾，但模式常不报低能见度，
导致规则/ML 漏判（如 2026-06-09/10/11）。

这里把「静态水体上下文」与「当日晨间气象」做交互，得到一个逐时
water_fog_signal ∈ [0,1]，仅供规则引擎使用：信号强时把边界日归为
Type B（水体蒸发型谷地雾），并配合降低 ML 融合权重，避免水体场景被 ML 压分。
"""
from __future__ import annotations

from app.engine.utils import bell_score, clamp, range_score

# 落差理想区间：水面到观景点约 400m 时填谷云海观赏性最佳（150–800m 有效）
IDEAL_ELEV_DIFF_M = 400.0
ELEV_DIFF_WIDTH_M = 450.0
# 水面有效面积归一（10km 内 ~30km² 即视为强水汽源）
AREA_REF_KM2 = 30.0
# 近水距离衰减半径
DISTANCE_REF_KM = 6.0


def water_static_factor(local_water: dict | None, elevation: float | None) -> float:
    """仅由静态水体上下文决定的强度（0–1），对点位恒定。"""
    if not local_water:
        return 0.0
    area = local_water.get("area_within_10km_km2")
    if area is None:
        area = local_water.get("area_total_km2")
    if area is None:
        return 0.0
    area_score = clamp(float(area) / AREA_REF_KM2)

    dist = local_water.get("nearest_distance_km")
    dist_score = (
        clamp((DISTANCE_REF_KM - float(dist)) / DISTANCE_REF_KM)
        if dist is not None
        else 0.3
    )

    surf = local_water.get("surface_elev_m")
    if surf is not None and elevation is not None:
        elev_score = bell_score(float(elevation) - float(surf), IDEAL_ELEV_DIFF_M, ELEV_DIFF_WIDTH_M)
    else:
        elev_score = 0.5

    return clamp(area_score * dist_score * elev_score)


def water_fog_meteo_factor(
    *,
    rh: float | None,
    temp: float | None,
    dewpoint: float | None,
    wind: float | None,
    cloud_high: float | None,
) -> float:
    """当日晨间气象对水体起雾的有利度（0–1）：高 RH + 小温度露点差 + 弱风 + 上层不太厚。"""
    rh_score = range_score(float(rh), 86.0, 100.0) if rh is not None else 0.0
    if temp is not None and dewpoint is not None:
        tdd_score = clamp((4.0 - (float(temp) - float(dewpoint))) / 4.0)
    else:
        tdd_score = 0.5
    wind_score = clamp((4.0 - float(wind)) / 4.0) if wind is not None else 0.5
    ch = float(cloud_high) if cloud_high is not None else 0.0
    clear_score = clamp((70.0 - ch) / 70.0)
    return clamp(0.4 * rh_score + 0.3 * tdd_score + 0.2 * wind_score + 0.1 * clear_score)


def resolve_local_water(terrain: dict | None) -> dict | None:
    if not terrain:
        return None
    return terrain.get("local_water")


def water_fog_signal_hour(
    local_water: dict | None,
    *,
    elevation: float | None,
    rh: float | None,
    temp: float | None,
    dewpoint: float | None,
    wind: float | None,
    cloud_high: float | None,
) -> float:
    """逐时水体晨雾潜势（0–1）。无水体上下文或静态强度为 0 时返回 0。"""
    static = water_static_factor(local_water, elevation)
    if static <= 0.0:
        return 0.0
    meteo = water_fog_meteo_factor(
        rh=rh, temp=temp, dewpoint=dewpoint, wind=wind, cloud_high=cloud_high
    )
    return clamp(static * meteo)
