from __future__ import annotations

from app.adapters.open_meteo import estimate_cloud_base
from app.engine.satellite_analyzer import build_satellite_factor
from app.engine.utils import bell_score, clamp, grade_from_probability, range_score
from app.models.schemas import FactorDetail, PredictionScore


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
) -> PredictionScore:
    cloud_base = estimate_cloud_base(temp, dewpoint)
    in_cloud_layer = cloud_base < elevation < cloud_base + 800

    rh_score = range_score(rh, 75, 95)
    mid_score = clamp((rh / 100.0) * 0.6 + bell_score(cloud_mid, 45, 35) * 0.4)
    low_score = bell_score(cloud_low, 50, 35)
    wind_score = bell_score(wind, 2.5, 4.0)
    rain_score = clamp(precip_recent / 8.0) * 0.5 + (1.0 if precip <= 0.2 else 0.3) * 0.5
    inversion_score = clamp((rh - 70) / 25.0) * 0.7 + (0.3 if cloud_mid < cloud_low + 15 else 0.1)
    elev_score = 1.0 if in_cloud_layer else bell_score(elevation - cloud_base, 200, 400)

    season_bonus = 0.05 if cloudsea_months and month in cloudsea_months else 0.0

    factors = {
        "humidity": FactorDetail(
            score=rh_score,
            weight=0.22,
            label="近地面湿度",
            description="湿度高时更容易形成云海",
            value=f"{rh:.0f}%",
        ),
        "mid_layer": FactorDetail(
            score=mid_score,
            weight=0.18,
            label="中低层湿度",
            description="下层湿润、中层适中，利于云海维持",
            value=f"中层云量 {cloud_mid:.0f}%",
        ),
        "low_cloud": FactorDetail(
            score=low_score,
            weight=0.15,
            label="低云量",
            description="低云适中时最易出现流动云海",
            value=f"{cloud_low:.0f}%",
        ),
        "wind": FactorDetail(
            score=wind_score,
            weight=0.12,
            label="风速",
            description="微风有利于云海堆积而不被吹散",
            value=f"{wind:.1f} m/s",
        ),
        "recent_rain": FactorDetail(
            score=rain_score,
            weight=0.13,
            label="雨后放晴",
            description="近48小时有降水后转晴，水汽充足",
            value=f"近48h降水 {precip_recent:.1f} mm",
        ),
        "inversion": FactorDetail(
            score=inversion_score,
            weight=0.10,
            label="逆温代理",
            description="下层湿、上层相对干，云层被压制在山腰",
            value="偏高" if inversion_score > 0.6 else "一般",
        ),
        "elevation_match": FactorDetail(
            score=elev_score,
            weight=0.10,
            label="海拔与云底",
            description="观景点位于估算云底与云顶之间最理想",
            value=f"云底≈{cloud_base:.0f}m / 观景点{elevation:.0f}m",
        ),
    }

    sat_adj, sat_factor = build_satellite_factor(
        satellite_context,
        (cloud_low + cloud_mid + cloud_high) / 3,
    )
    if sat_factor:
        scale = 0.88
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
