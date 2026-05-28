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


def _fog_not_cloudsea_score(*, rh: float, cloud_low: float, visibility: float | None) -> float:
    """高湿+极低云+低能见度 → 雾/轻雾，非层状云海（梵净山 RH-温度-云关系）。"""
    if cloud_low >= 15:
        return 1.0
    if rh < 85:
        return 1.0
    if visibility is None:
        if cloud_low >= 8:
            return 0.85
        return clamp(0.35 + cloud_low / 20.0)
    vis_km = visibility / 1000.0
    if vis_km >= 5.0:
        return 1.0
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
    cloud_base = estimate_cloud_base(temp, dewpoint)

    rh_score = range_score(rh, 75, 95)
    mid_score = clamp((rh / 100.0) * 0.6 + bell_score(cloud_mid, 45, 35) * 0.4)
    low_score = _score_low_cloud_direct(cloud_low)
    wind_score = bell_score(wind, 2.5, 4.0)
    rain_score = clamp(precip_recent / 8.0) * 0.5 + (1.0 if precip <= 0.2 else 0.3) * 0.5
    elev_score = _score_elevation_match(cloud_low=cloud_low, cloud_base=cloud_base, elevation=elevation)
    fog_score = _fog_not_cloudsea_score(rh=rh, cloud_low=cloud_low, visibility=visibility)

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
            description="层云/碎层云是云海直接证据；极低低云时高湿仅代表雾，非云海",
            value=f"{cloud_low:.0f}%",
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
            description="高湿+低能见度+极低低云 → 雾/轻雾，非观赏级云海",
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

    presence = _cloud_presence_factor(cloud_low, cloud_mid)
    weighted *= 0.22 + 0.78 * presence

    if cloud_low < 10 and fog_score < 0.5:
        weighted = min(weighted, 0.32)
    if has_pressure_profile and t_850 is not None and t_925 is not None and (t_850 - t_925) <= 0 and cloud_low < 20:
        weighted = min(weighted, 0.38)

    probability = int(round(clamp(weighted) * 100))

    return PredictionScore(
        probability=probability,
        grade=grade_from_probability(probability),
        factors=factors,
        cloud_base_m=round(cloud_base, 1),
    )
