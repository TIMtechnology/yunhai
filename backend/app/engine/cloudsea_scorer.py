from __future__ import annotations

from app.adapters.open_meteo import estimate_cloud_base
from app.engine.satellite_analyzer import build_satellite_factor
from app.engine.utils import bell_score, clamp, grade_from_probability, range_score
from app.models.schemas import FactorDetail, PredictionScore

# 北京高山云海模糊逻辑预报（Phase A 垂直场因子文献）
REF_BEIJING_CLOUDSEA = "10.3878/j.issn.1006-9585.2025.25032"


def _score_rh_850(rh: float) -> float:
    """850 hPa 以下湿润层：文献阈值 RH > 80%。"""
    return range_score(rh, 80, 95)


def _score_rh_700_upper_dry(rh: float) -> float:
    """850 hPa 以上相对干燥：以 700 hPa RH 作上干代理，越低越好。"""
    if rh <= 60:
        return clamp(0.55 + (60 - rh) / 80.0)
    return clamp(0.55 - (rh - 60) / 80.0)


def _score_inversion_850_925(t_850: float, t_925: float) -> float:
    """925→850 hPa 逆温强度：T850 − T925，正值表示随高度升温。"""
    delta = t_850 - t_925
    if delta <= 0:
        return clamp(0.15 + delta / 12.0)
    return bell_score(delta, 4.0, 4.5)


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
) -> PredictionScore:
    cloud_base = estimate_cloud_base(temp, dewpoint)
    in_cloud_layer = cloud_base < elevation < cloud_base + 800

    rh_score = range_score(rh, 75, 95)
    mid_score = clamp((rh / 100.0) * 0.6 + bell_score(cloud_mid, 45, 35) * 0.4)
    low_score = bell_score(cloud_low, 50, 35)
    wind_score = bell_score(wind, 2.5, 4.0)
    rain_score = clamp(precip_recent / 8.0) * 0.5 + (1.0 if precip <= 0.2 else 0.3) * 0.5
    elev_score = 1.0 if in_cloud_layer else bell_score(elevation - cloud_base, 200, 400)

    has_pressure_profile = (
        rh_850 is not None
        and rh_700 is not None
        and t_850 is not None
        and t_925 is not None
    )

    if has_pressure_profile:
        rh850_score = _score_rh_850(rh_850)
        rh700_score = _score_rh_700_upper_dry(rh_700)
        inversion_score = _score_inversion_850_925(t_850, t_925)
        inversion_value = f"ΔT={t_850 - t_925:+.1f}°C"
    else:
        rh850_score = rh700_score = inversion_score = 0.0
        inversion_value = "气压层数据缺失"

    season_bonus = 0.05 if cloudsea_months and month in cloudsea_months else 0.0

    factors: dict[str, FactorDetail] = {
        "rh_850": FactorDetail(
            score=rh850_score if has_pressure_profile else rh_score,
            weight=0.18 if has_pressure_profile else 0.0,
            label="850 hPa 湿度",
            description="低层（850 hPa 以下）充分湿润，利于云海形成",
            value=f"{rh_850:.0f}%" if rh_850 is not None else "—",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "rh_700": FactorDetail(
            score=rh700_score if has_pressure_profile else 0.0,
            weight=0.12 if has_pressure_profile else 0.0,
            label="700 hPa 上干",
            description="850 hPa 以上相对干燥，抑制云垂直发展、利于平流层状云",
            value=f"{rh_700:.0f}%" if rh_700 is not None else "—",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "t_inversion_850_925": FactorDetail(
            score=inversion_score if has_pressure_profile else 0.0,
            weight=0.12 if has_pressure_profile else 0.0,
            label="925–850 hPa 逆温",
            description="低层逆温将水汽与云系压制在山腰附近",
            value=inversion_value,
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "humidity": FactorDetail(
            score=rh_score,
            weight=0.10,
            label="近地面湿度",
            description="2 m 相对湿度，补充近地面水汽条件",
            value=f"{rh:.0f}%",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "mid_layer": FactorDetail(
            score=mid_score,
            weight=0.10,
            label="中低层湿度",
            description="下层湿润、中层云量适中，利于云海维持",
            value=f"中层云量 {cloud_mid:.0f}%",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "low_cloud": FactorDetail(
            score=low_score,
            weight=0.12,
            label="低云量",
            description="低云适中时最易出现流动云海",
            value=f"{cloud_low:.0f}%",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "wind": FactorDetail(
            score=wind_score,
            weight=0.08,
            label="风速",
            description="微风有利于云海堆积而不被吹散",
            value=f"{wind:.1f} m/s",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "recent_rain": FactorDetail(
            score=rain_score,
            weight=0.10,
            label="雨后放晴",
            description="近 48 小时有降水后转晴，水汽充足",
            value=f"近48h降水 {precip_recent:.1f} mm",
            reference=REF_BEIJING_CLOUDSEA,
        ),
        "elevation_match": FactorDetail(
            score=elev_score,
            weight=0.08,
            label="海拔与云底",
            description="观景点位于估算云底与云顶之间最理想",
            value=f"云底≈{cloud_base:.0f}m / 观景点{elevation:.0f}m",
            reference="",
        ),
    }

    if not has_pressure_profile:
        legacy_inversion = clamp((rh - 70) / 25.0) * 0.7 + (0.3 if cloud_mid < cloud_low + 15 else 0.1)
        factors["inversion"] = FactorDetail(
            score=legacy_inversion,
            weight=0.22,
            label="逆温代理",
            description="气压层数据不可用时，以近地面 RH 与云量结构近似",
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
    probability = int(round(clamp(weighted) * 100))

    return PredictionScore(
        probability=probability,
        grade=grade_from_probability(probability),
        factors=factors,
        cloud_base_m=round(cloud_base, 1),
    )
