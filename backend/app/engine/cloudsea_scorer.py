from __future__ import annotations

from app.adapters.open_meteo import estimate_cloud_base
from app.engine.satellite_analyzer import build_satellite_factor
from app.engine.utils import bell_score, clamp, grade_from_probability, range_score
from app.models.schemas import FactorDetail, PredictionScore

# 北京高山云海模糊逻辑预报（Phase A 垂直场因子）
REF_BEIJING_CLOUDSEA = "10.3878/j.issn.1006-9585.2025.25032"
# 梵净山：RH 与温度共同决定云海；无云量时高湿不等于可观赏云海
REF_FANJINGSHAN = "10.13718/j.cnki.xdzk.2023.06.017"
# 庐山：850 hPa 高湿区 + 山腰逆温
REF_LUSHAN = "10.11676/qxxb2023.20220188"


def _score_rh_850(rh: float) -> float:
    return range_score(rh, 80, 95)


def _score_rh_700_upper_dry(rh: float) -> float:
    if rh <= 60:
        return clamp(0.55 + (60 - rh) / 80.0)
    return clamp(0.55 - (rh - 60) / 80.0)


def _score_inversion_850_925(t_850: float, t_925: float) -> tuple[float, str]:
    delta = t_850 - t_925
    if delta <= 0:
        score = clamp(0.12 + delta / 10.0)
        desc = "无明显逆温，水汽易垂直扩散，不利于云海堆积"
    else:
        score = bell_score(delta, 4.0, 4.5)
        desc = "低层逆温将水汽与云系压制在山腰附近"
    return score, desc


def _classify_cloudsea_archetype(
    *,
    cloud_low: float,
    cloud_mid: float,
    visibility: float | None,
    rh: float,
    rh_850: float | None,
    precip_recent: float,
    t_850: float | None = None,
    t_925: float | None = None,
) -> tuple[str, str]:
    """五女山金标准日归纳：TypeA 高能见度山谷云海 / TypeB 低能见度层云 / 雾型排除。"""
    if cloud_mid >= 40 and rh >= 90 and visibility is not None and visibility <= 500:
        return "fog_exclude", "中层云偏高+近饱和湿度，雾/层云型"
    if (
        rh >= 93
        and visibility is not None
        and visibility <= 500
        and cloud_mid <= 10
        and cloud_low <= 15
    ):
        return "fog_exclude", "近地面饱和雾，非观赏云海"
    if cloud_low >= 40 and rh >= 90:
        return "fog_exclude", "模式低云偏高+高湿，非山谷云海型"
    if (
        cloud_mid <= 10
        and cloud_low <= 10
        and visibility is not None
        and visibility >= 5000
        and rh_850 is not None
        and rh_850 <= 55
        and precip_recent <= 10
        and _type_a_moisture_support(rh=rh, cloud_low=cloud_low, cloud_mid=cloud_mid, precip_recent=precip_recent)
        and rh_850 >= 42
        and not _strong_negative_inversion(t_850, t_925)
    ):
        return "type_a", "高能见度山谷云海型"
    if (
        cloud_mid <= 15
        and visibility is not None
        and visibility <= 500
        and rh <= 85
        and rh >= 60
        and rh_850 is not None
        and rh_850 <= 45
    ):
        return "type_b", "低能见度层云型"
    return "neutral", ""


def _strong_negative_inversion(t_850: float | None, t_925: float | None, threshold: float = -2.5) -> bool:
    if t_850 is None or t_925 is None:
        return False
    return (t_850 - t_925) <= threshold


def _type_a_moisture_support(
    *,
    rh: float,
    cloud_low: float,
    cloud_mid: float,
    precip_recent: float,
) -> bool:
    """Type A 补偿仅在有近地面湿润或降水背景时启用，避免晴天空天误判。"""
    if precip_recent >= 0.5:
        return True
    if cloud_low >= 5 or cloud_mid >= 5:
        return True
    return rh >= 68


def cloudsea_plausibility_cap(
    *,
    cloud_low: float,
    cloud_mid: float,
    rh: float,
    rh_850: float | None,
    rh_700: float | None,
    t_850: float | None,
    t_925: float | None,
    visibility: float | None,
    archetype: str,
) -> int:
    """基于当前观测场的云海概率硬上限（0–100），用于约束 ML 与规则融合结果。"""
    cap = 100
    inversion = (t_850 - t_925) if t_850 is not None and t_925 is not None else None
    cloud_signal = max(cloud_low, cloud_mid)

    if cloud_low < 5 and cloud_mid < 5:
        cap = min(cap, 42)
    if rh_850 is not None and rh_850 < 50:
        cap = min(cap, 40)
    if rh_700 is not None and rh_700 > 75 and rh_850 is not None and rh_850 < 55:
        cap = min(cap, 38)
    if inversion is not None and inversion <= -2.0:
        cap = min(cap, 36)
    if (
        rh_850 is not None
        and rh_850 < 50
        and inversion is not None
        and inversion <= 0
        and cloud_signal < 8
    ):
        cap = min(cap, 28)
    if archetype == "neutral" and cloud_signal < 10 and rh < 72:
        cap = min(cap, 32)
    if visibility is not None and visibility >= 12000 and cloud_signal < 5 and rh < 75:
        cap = min(cap, 30)
    return cap


def _infer_effective_low_cloud(
    *,
    cloud_low: float,
    cloud_mid: float,
    visibility: float | None,
    elevation: float,
    rh: float,
    archetype: str = "neutral",
) -> tuple[float, str]:
    """NWP 网格低云量常低估山顶/谷地层云；极低能见度+高海拔作补偿（Open-Meteo 自身矛盾）。"""
    if archetype == "type_a":
        return max(cloud_low, 30.0), "高能见度山谷云海型，补偿局域层云"
    if archetype == "fog_exclude":
        return cloud_low, ""

    if elevation < 500 or visibility is None:
        return cloud_low, ""
    if cloud_low >= 20:
        return cloud_low, ""

    vis_m = visibility
    if rh >= 90:
        return cloud_low, ""
    if archetype == "type_b":
        if vis_m <= 300:
            return max(cloud_low, 45.0), f"能见度 {vis_m:.0f}m，低能见度层云型"
        if vis_m <= 500:
            return max(cloud_low, 35.0), f"能见度 {vis_m:.0f}m，低能见度层云型"
    if vis_m <= 300:
        return max(cloud_low, 45.0), f"能见度 {vis_m:.0f}m，推断局域层云/云海"
    if vis_m <= 800:
        return max(cloud_low, 30.0), f"能见度 {vis_m:.0f}m，推断云雾层"
    if vis_m <= 1500 and rh >= 85 and cloud_mid >= 8:
        return max(cloud_low, 20.0), f"能见度 {vis_m:.0f}m，高湿雾区"
    return cloud_low, ""


def _score_low_cloud_direct(cloud_low: float) -> float:
    """低云是云海直接证据；极低低云时不应给高分（梵净山/华山低云状研究）。"""
    if cloud_low < 5:
        return clamp(cloud_low / 20.0)
    if cloud_low < 20:
        return clamp(0.15 + (cloud_low - 5) / 40.0)
    return bell_score(cloud_low, 45, 35)


def _score_elevation_match(
    *,
    cloud_low: float,
    cloud_base: float,
    elevation: float,
) -> float:
    """观景点与云底匹配；无实质低云时云底估算无意义。"""
    if cloud_low < 15:
        return clamp(cloud_low / 30.0) * 0.25
    in_cloud_layer = cloud_base < elevation < cloud_base + 800
    if in_cloud_layer:
        return 1.0
    return bell_score(elevation - cloud_base, 200, 400)


def _cloud_presence_factor(cloud_low: float, cloud_mid: float) -> float:
    """综合低/中层云量，作为云海可见性的硬门控（0–1）。"""
    signal = max(cloud_low, cloud_mid * 0.4)
    return clamp(signal / 35.0)


def _fog_not_cloudsea_score(
    *,
    rh: float,
    cloud_low: float,
    visibility: float | None,
    elevation: float,
    vis_proxy_note: str,
) -> float:
    """区分高山层云（低能见度+有补偿信号）与近地面雾。"""
    if vis_proxy_note:
        return clamp(0.65 + min(cloud_low, 60) / 120.0)
    if cloud_low >= 15:
        return 1.0
    if rh < 85:
        return 1.0
    if visibility is None:
        return clamp(0.35 + cloud_low / 20.0)
    vis_km = visibility / 1000.0
    if vis_km >= 5.0:
        return 1.0
    if elevation >= 500 and vis_km <= 1.0:
        return clamp(0.55 + vis_km / 4.0)
    if vis_km <= 2.0 and cloud_low < 10:
        return clamp(0.15 + vis_km / 8.0)
    return clamp(0.45 + vis_km / 10.0)


def score_cloudsea(
    *,
    rh: float,
    cloud_low: float,
    cloud_mid: float,
    cloud_high: float = 0.0,
    wind: float,
    precip: float,
    precip_recent: float,
    temp: float,
    dewpoint: float,
    elevation: float,
    month: int,
    cloudsea_months: list[int] | None = None,
    satellite_context: dict | None = None,
    rh_850: float | None = None,
    rh_700: float | None = None,
    t_850: float | None = None,
    t_925: float | None = None,
    visibility: float | None = None,
) -> PredictionScore:
    archetype, archetype_note = _classify_cloudsea_archetype(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        rh=rh,
        rh_850=rh_850,
        precip_recent=precip_recent,
        t_850=t_850,
        t_925=t_925,
    )
    effective_low, vis_proxy_note = _infer_effective_low_cloud(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        elevation=elevation,
        rh=rh,
        archetype=archetype,
    )
    cloud_base = estimate_cloud_base(temp, dewpoint)

    rh_score = range_score(rh, 75, 95)
    mid_score = clamp((rh / 100.0) * 0.6 + bell_score(cloud_mid, 45, 35) * 0.4)
    low_score = _score_low_cloud_direct(effective_low)
    wind_score = bell_score(wind, 2.5, 4.0)
    rain_score = clamp(precip_recent / 8.0) * 0.5 + (1.0 if precip <= 0.2 else 0.3) * 0.5
    elev_score = _score_elevation_match(
        cloud_low=effective_low, cloud_base=cloud_base, elevation=elevation
    )
    fog_score = _fog_not_cloudsea_score(
        rh=rh,
        cloud_low=effective_low,
        visibility=visibility,
        elevation=elevation,
        vis_proxy_note=vis_proxy_note,
    )

    has_pressure_profile = (
        rh_850 is not None
        and rh_700 is not None
        and t_850 is not None
        and t_925 is not None
    )

    inversion_desc = "气压层数据缺失"
    if has_pressure_profile:
        rh850_score = _score_rh_850(rh_850)
        rh700_score = _score_rh_700_upper_dry(rh_700)
        inversion_score, inversion_desc = _score_inversion_850_925(t_850, t_925)
        inversion_value = f"ΔT={t_850 - t_925:+.1f}°C"
    else:
        rh850_score = rh700_score = inversion_score = 0.0
        inversion_value = "气压层数据缺失"

    season_bonus = 0.05 if cloudsea_months and month in cloudsea_months else 0.0

    factors: dict[str, FactorDetail] = {
        "low_cloud": FactorDetail(
            score=low_score,
            weight=0.20,
            label="低云量",
            description="层云/碎层云是云海直接证据；模式低估时用能见度补偿",
            value=(
                f"模式{cloud_low:.0f}% → 有效{effective_low:.0f}% ({vis_proxy_note})"
                if vis_proxy_note
                else (
                    f"{cloud_low:.0f}% · {archetype_note}"
                    if archetype_note and archetype != "neutral"
                    else f"{cloud_low:.0f}%"
                )
            ),
            reference=REF_FANJINGSHAN,
        ),
        "rh_850": FactorDetail(
            score=rh850_score if has_pressure_profile else rh_score,
            weight=0.16 if has_pressure_profile else 0.0,
            label="850 hPa 湿度",
            description="低层充分湿润，利于云海形成",
            value=f"{rh_850:.0f}%" if rh_850 is not None else "—",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "rh_700": FactorDetail(
            score=rh700_score if has_pressure_profile else 0.0,
            weight=0.10 if has_pressure_profile else 0.0,
            label="700 hPa 上干",
            description="850 hPa 以上相对干燥，利于层状云平流",
            value=f"{rh_700:.0f}%" if rh_700 is not None else "—",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "t_inversion_850_925": FactorDetail(
            score=inversion_score if has_pressure_profile else 0.0,
            weight=0.12 if has_pressure_profile else 0.0,
            label="925–850 hPa 逆温",
            description=inversion_desc,
            value=inversion_value,
            reference=REF_LUSHAN,
        ),
        "humidity": FactorDetail(
            score=rh_score,
            weight=0.08,
            label="近地面湿度",
            description="2 m 相对湿度；高湿 alone 不足以判定云海",
            value=f"{rh:.0f}%",
            reference=REF_FANJINGSHAN,
        ),
        "mid_layer": FactorDetail(
            score=mid_score,
            weight=0.08,
            label="中低层湿度",
            description="中层云量适中利于云海维持",
            value=f"中层云量 {cloud_mid:.0f}%",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "wind": FactorDetail(
            score=wind_score,
            weight=0.06,
            label="风速",
            description="微风利于云海堆积；极弱风+高湿易滞留雾",
            value=f"{wind:.1f} m/s",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "recent_rain": FactorDetail(
            score=rain_score,
            weight=0.08,
            label="雨后放晴",
            description="近 48h 降水后转晴，水汽来源之一（非充分条件）",
            value=f"近48h降水 {precip_recent:.1f} mm",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "elevation_match": FactorDetail(
            score=elev_score,
            weight=0.06,
            label="海拔与云底",
            description="有低云时观景点位于云底–云顶之间最理想；无云时该项降权",
            value=f"云底≈{cloud_base:.0f}m / 观景点{elevation:.0f}m",
            reference=REF_FANJINGSHAN,
        ),
        "fog_vs_cloudsea": FactorDetail(
            score=fog_score,
            weight=0.06,
            label="雾 vs 云海",
            description="低能见度在高海拔可指示层云；近地面高湿雾则降权",
            value=(
                f"能见度 {visibility / 1000:.1f} km"
                if visibility is not None
                else "能见度未知"
            ),
            reference=REF_FANJINGSHAN,
        ),
    }

    if not has_pressure_profile:
        legacy_inversion = clamp((rh - 70) / 25.0) * 0.7 + (0.3 if cloud_mid < cloud_low + 15 else 0.1)
        factors["inversion"] = FactorDetail(
            score=legacy_inversion,
            weight=0.18,
            label="逆温代理",
            description="气压层不可用时，以 RH 与云量结构近似",
            value="偏高" if legacy_inversion > 0.6 else "一般",
            reference=REF_BEIJING_CLOUDSEA,
        )
        factors.pop("rh_850")
        factors.pop("rh_700")
        factors.pop("t_inversion_850_925")

    sat_adj, sat_factor = build_satellite_factor(
        satellite_context,
        (cloud_low + cloud_mid + cloud_high) / 3,
    )
    if sat_factor:
        scale = 0.94
        for detail in factors.values():
            detail.weight = round(detail.weight * scale, 4)
        factors["satellite_ir"] = sat_factor
        season_bonus += sat_adj

    weighted = sum(f.score * f.weight for f in factors.values()) + season_bonus

    presence = _cloud_presence_factor(effective_low, cloud_mid)
    if archetype == "type_a":
        presence = max(presence, 0.55)
    weighted *= 0.22 + 0.78 * presence

    if archetype == "fog_exclude":
        weighted = min(weighted, 0.25)
    elif archetype == "type_a":
        weighted = max(weighted, 0.58)
    elif archetype == "type_b":
        weighted = max(weighted, 0.48)

    if cloud_low < 10 and not vis_proxy_note and fog_score < 0.5 and archetype == "neutral":
        weighted = min(weighted, 0.32)
    if (
        has_pressure_profile
        and t_850 is not None
        and t_925 is not None
        and (t_850 - t_925) <= 0
        and effective_low < 20
        and not vis_proxy_note
        and archetype not in ("type_a", "type_b")
    ):
        weighted = min(weighted, 0.38)

    probability = int(round(clamp(weighted) * 100))

    return PredictionScore(
        probability=probability,
        grade=grade_from_probability(probability),
        factors=factors,
        cloud_base_m=round(cloud_base, 1),
    )


def cloudsea_visual_evidence(
    *,
    cloud_low: float,
    cloud_mid: float,
    visibility: float | None,
    elevation: float,
    rh: float,
    rh_850: float | None = None,
    precip_recent: float = 0.0,
) -> tuple[float, bool]:
    """供场景标签判定：返回有效低云量及是否有云海可见证据。"""
    archetype, _ = _classify_cloudsea_archetype(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        rh=rh,
        rh_850=rh_850,
        precip_recent=precip_recent,
        t_850=t_850,
        t_925=t_925,
    )
    if archetype == "fog_exclude":
        return cloud_low, False
    effective_low, vis_note = _infer_effective_low_cloud(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        elevation=elevation,
        rh=rh,
        archetype=archetype,
    )
    has = archetype in ("type_a", "type_b") or (
        effective_low >= 20
        or (effective_low >= 10 and cloud_mid >= 15)
        or bool(vis_note)
    )
    return effective_low, has
