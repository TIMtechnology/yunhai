from __future__ import annotations

from app.adapters.open_meteo import estimate_cloud_base
from app.engine.observable_field import (
    compute_observable_field,
    observable_cloudsea_evidence,
    score_observable_field,
)
from app.engine.satellite_analyzer import build_satellite_factor
from app.engine.utils import bell_score, clamp, grade_from_probability, range_score
from app.models.schemas import FactorDetail, PredictionScore

try:
    from app.adapters.dem import estimate_cloud_top_m
except ImportError:
    def estimate_cloud_top_m(cloud_base_m, cloud_low_pct, cloud_mid_pct):  # type: ignore
        return cloud_base_m + (cloud_low_pct / 100.0) * 450.0

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
    viewing_mode: str = "valley_fill",
    observable: dict | None = None,
    water_fog_signal: float = 0.0,
) -> tuple[str, str]:
    """五女山金标准日归纳 + 峰顶俯瞰 Type C。"""
    if (
        viewing_mode == "peak_overlook"
        and rh_850 is not None
        and rh_850 >= 72
        and cloud_low >= 35
        and observable
        and observable.get("viewer_above_cloud")
        and not _strong_negative_inversion(t_850, t_925)
    ):
        return "type_c", "峰顶逆温层·谷地云海（峰顶高云）"
    if (
        viewing_mode == "peak_overlook"
        and observable
        and observable.get("viewer_above_cloud")
        and float(observable.get("observable_fraction") or 0) >= 0.22
        and rh_850 is not None
        and rh_850 >= 58
    ):
        sector_pts = int(observable.get("sector_meteo_points") or 0)
        sector_low = observable.get("sector_cloud_low_mean")
        if sector_pts > 0 and sector_low is not None and float(sector_low) < 10:
            pass
        else:
            return "type_c", "峰顶俯瞰·日出扇区可观测云海"
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
        peak_inversion = (
            viewing_mode == "peak_overlook"
            and rh_850 is not None
            and rh_850 >= 68
            and (
                (observable and observable.get("viewer_above_cloud"))
                or cloud_low >= 70
            )
        )
        if not peak_inversion:
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
        and rh <= 90
        and rh >= 60
        and rh_850 is not None
        and rh_850 <= 45
    ):
        return "type_b", "低能见度层云型"
    # 高湿谷地雾型：模式低云偏少但近地面饱和+低能见度，常见于五女山夏季清晨
    if (
        cloud_mid <= 15
        and cloud_low <= 15
        and visibility is not None
        and visibility <= 500
        and rh >= 78
        and rh_850 is not None
        and rh_850 <= 75
    ):
        return "type_b", "低能见度层云型（高湿谷地雾）"
    # 层云/雾复合型：中云增厚但仍近地面饱和+低能见度（过渡性清晨）
    if (
        15 <= cloud_mid <= 35
        and visibility is not None
        and visibility <= 500
        and rh >= 82
        and cloud_low <= 35
    ):
        return "type_b", "低能见度层云/雾复合型"
    # 水体蒸发型谷地雾：山脚大型水库 + 弱风高湿 + 模式确报低能见度。
    # 经验上 ~11km 网格的静态水体信号无法把真云海日与晴空强风日分开
    # （二者 water_fog_signal 接近），故必须叠加「模式报低能见度」这一硬门，
    # 仅补救 RH 略低于常规阈值（72–78）而险些漏判的近库低能见度日（如 6/11 04:00）。
    # 注意：近库辐射雾/平流雾常伴随 700–850hPa 干廓线（ΔT 明显为负），
    # 不能用上层逆温门排除，否则会把 6/9–6/11 等真云海日压成 neutral + 低 cap。
    if (
        water_fog_signal >= 0.30
        and visibility is not None
        and visibility <= 600
        and rh >= 72
        and cloud_mid <= 35
        and cloud_low <= 45
    ):
        return "type_b", "水体蒸发型谷地雾（近库+弱风+低能见度）"
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
    viewing_mode: str = "valley_fill",
    observable_fraction: float | None = None,
    elev_max_5km: float | None = None,
    elevation: float | None = None,
    cloud_base: float | None = None,
) -> int:
    """基于当前观测场的云海概率硬上限（0–100），用于约束 ML 与规则融合结果。"""
    if viewing_mode == "peak_overlook" and observable_fraction is not None and observable_fraction >= 0.35:
        if rh_850 is not None and rh_850 >= 58:
            return 100
    if viewing_mode == "peak_overlook" and elev_max_5km and elevation and cloud_base is not None:
        if (
            cloud_base < elev_max_5km
            and elevation > elev_max_5km - 100
            and rh_850 is not None
            and rh_850 >= 62
        ):
            return 100
    # 五女山金标准型态（Type A/B）下，低 RH850 + 模式无云仍可能有观赏级云海，不做晴空干廓线上限
    if archetype in ("type_a", "type_b", "type_c"):
        return 100

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


def cloudsea_plausibility_from_profile(
    profile_hour: dict | None,
    *,
    elevation_m: float,
    viewing_mode: str = "valley_fill",
) -> int | None:
    """Estimate an additional plausibility cap from vertical cloud profile.

    This is intentionally conservative: it only tightens clearly dry/thin low-cloud
    profiles and leaves strong below-viewpoint cloud signals uncapped.
    """
    if not profile_hour:
        return None
    levels = profile_hour.get("levels") or []
    if not levels:
        return None
    below = [
        l
        for l in levels
        if l.get("height_m_asl") is not None and float(l["height_m_asl"]) <= elevation_m + 100
    ]
    low_cloud = [float(l.get("cloud_cover_pct") or 0) for l in below]
    low_rh = [float(l.get("rh_pct") or 0) for l in below if l.get("rh_pct") is not None]
    if low_cloud and max(low_cloud) >= 55 and (not low_rh or max(low_rh) >= 75):
        return 100
    if low_cloud and max(low_cloud) <= 12 and (not low_rh or max(low_rh) < 80):
        return 35 if viewing_mode == "peak_overlook" else 30
    return None


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
    # 五女山等 valley_fill：NWP 低云=0 但贴地能见度差 + 高湿 → 谷雾/辐射雾代理
    if (
        500 <= elevation <= 1200
        and cloud_low < 12
        and vis_m <= 2000
        and rh >= 65
    ):
        boost = 35.0
        if vis_m <= 500:
            boost = 55.0
        elif vis_m <= 1000:
            boost = 45.0
        if rh >= 80:
            boost += 8.0
        return max(cloud_low, boost), f"能见度 {vis_m:.0f}m·高湿，推断谷地雾/云海"
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


def _score_peak_overlook_geometry(
    *,
    elevation: float,
    cloud_base: float,
    cloud_top: float,
    elev_max_5km: float,
    rh_850: float | None,
) -> tuple[float, str]:
    """峰顶俯瞰：云在脚下谷地、人在云上时加分。"""
    valley_peak = elev_max_5km
    if elevation < cloud_base:
        return 0.12, f"云底 {cloud_base:.0f}m 高于观景点，难以俯瞰"
    below_viewer = cloud_top < elevation and cloud_base < valley_peak
    if below_viewer:
        gap = max(valley_peak - cloud_base, 0.0)
        score = clamp(0.55 + bell_score(gap, 250, 350) * 0.35)
        if rh_850 is not None and rh_850 >= 65:
            score = max(score, 0.72)
        return score, f"脚下谷地云底≈{cloud_base:.0f}m·5km峰{valley_peak:.0f}m"
    if cloud_base < elevation < cloud_top:
        return 0.45, "观景点处于云缘层"
    if elevation > cloud_top:
        return 0.35, "观景点在云上但谷地云系偏弱"
    return 0.25, "峰顶几何条件一般"


def _peak_valley_presence(
    *,
    rh_850: float | None,
    rh_700: float | None,
    cloud_base: float,
    elev_max_5km: float,
    elevation: float,
    cloud_low: float,
    cloud_mid: float,
    sector_cloud_low: float | None = None,
) -> float:
    """峰顶模式：用谷地湿润与层结几何替代单点低云门控。"""
    if sector_cloud_low is not None and float(sector_cloud_low) < 8:
        return 0.18
    moisture = 0.0
    if rh_850 is not None:
        moisture = max(moisture, range_score(rh_850, 60, 90) * 0.55)
    if rh_700 is not None:
        moisture = max(moisture, range_score(rh_700, 55, 85) * 0.35)
    geometry = 0.0
    if cloud_base < elev_max_5km and elevation > elev_max_5km - 150:
        geometry = clamp(0.45 + bell_score(elev_max_5km - cloud_base, 200, 400) * 0.45)
    signal = max(cloud_low, cloud_mid * 0.35, moisture * 100 * 0.25)
    return clamp(max(0.22 + 0.78 * clamp(signal / 35.0), geometry, moisture))


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
    viewing_mode: str = "valley_fill",
    terrain: dict | None = None,
    sunrise_azimuth_deg: float | None = None,
    sector_meteo: list[dict] | None = None,
    summit_cloud_low: float | None = None,
    summit_rh: float | None = None,
    water_fog_signal: float | None = None,
) -> PredictionScore:
    if water_fog_signal is None:
        from app.engine.water_context import water_fog_signal_hour

        water_fog_signal = water_fog_signal_hour(
            (terrain or {}).get("local_water"),
            elevation=elevation,
            rh=rh,
            temp=temp,
            dewpoint=dewpoint,
            wind=wind,
            cloud_high=cloud_high,
        )
    cloud_base_pre = estimate_cloud_base(temp, dewpoint)
    cloud_top_pre = estimate_cloud_top_m(cloud_base_pre, cloud_low, cloud_mid)
    elev_max_5km = float((terrain or {}).get("elev_max_5km_m") or elevation)
    is_peak = viewing_mode == "peak_overlook"
    sun_az = sunrise_azimuth_deg or (terrain or {}).get("sunrise_azimuth_deg")

    obs_pre = compute_observable_field(
        viewer_elev_m=elevation,
        cloud_base_m=cloud_base_pre,
        cloud_top_m=cloud_top_pre,
        visibility_m=visibility,
        elev_profile_sunrise=(terrain or {}).get("elev_profile_sunrise"),
        viewing_mode=viewing_mode,
        rh_850=rh_850,
        rh_700=rh_700,
        sunrise_azimuth_deg=sun_az,
        elev_max_5km_m=elev_max_5km,
        sector_meteo=sector_meteo,
        summit_cloud_low=float(summit_cloud_low if summit_cloud_low is not None else cloud_low),
        summit_rh=float(summit_rh if summit_rh is not None else rh),
    )

    archetype, archetype_note = _classify_cloudsea_archetype(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        rh=rh,
        rh_850=rh_850,
        precip_recent=precip_recent,
        t_850=t_850,
        t_925=t_925,
        viewing_mode=viewing_mode,
        observable=obs_pre,
        water_fog_signal=water_fog_signal,
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
    cloud_top = estimate_cloud_top_m(cloud_base, effective_low, cloud_mid)

    observable = compute_observable_field(
        viewer_elev_m=elevation,
        cloud_base_m=cloud_base,
        cloud_top_m=cloud_top,
        visibility_m=visibility,
        elev_profile_sunrise=(terrain or {}).get("elev_profile_sunrise"),
        viewing_mode=viewing_mode,
        rh_850=rh_850,
        rh_700=rh_700,
        sunrise_azimuth_deg=sun_az,
        elev_max_5km_m=elev_max_5km,
        sector_meteo=sector_meteo,
        summit_cloud_low=effective_low,
        summit_rh=rh,
    )

    if is_peak:
        elev_score, elev_desc = score_observable_field(observable)
    else:
        elev_score = _score_elevation_match(
            cloud_low=effective_low, cloud_base=cloud_base, elevation=elevation
        )
        elev_desc = f"云底≈{cloud_base:.0f}m / 观景点{elevation:.0f}m"

    rh_score = range_score(rh, 75, 95)
    mid_score = clamp((rh / 100.0) * 0.6 + bell_score(cloud_mid, 45, 35) * 0.4)
    low_score = _score_low_cloud_direct(effective_low)
    if is_peak:
        sector_low = observable.get("sector_cloud_low_mean")
        if sector_low is not None:
            low_score = max(low_score, _score_low_cloud_direct(float(sector_low)))
    wind_score = bell_score(wind, 2.5, 4.0)
    rain_score = clamp(precip_recent / 8.0) * 0.5 + (1.0 if precip <= 0.2 else 0.3) * 0.5
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
            weight=0.12 if is_peak else 0.06,
            label="可观测云海场" if is_peak else "海拔与云底",
            description=elev_desc if is_peak else "有低云时观景点位于云底–云顶之间最理想；无云时该项降权",
            value=(
                f"可观测占比 {observable['observable_fraction']:.0%} · 可见{observable['visible_range_km']:.0f}km"
                if is_peak
                else f"云底≈{cloud_base:.0f}m / 观景点{elevation:.0f}m"
            ),
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
    if is_peak:
        presence = max(
            presence,
            float(observable.get("observable_fraction") or 0.0),
            _peak_valley_presence(
                rh_850=rh_850,
                rh_700=rh_700,
                cloud_base=cloud_base,
                elev_max_5km=elev_max_5km,
                elevation=elevation,
                cloud_low=effective_low,
                cloud_mid=cloud_mid,
                sector_cloud_low=observable.get("sector_cloud_low_mean"),
            ),
        )
    if archetype == "type_a":
        presence = max(presence, 0.55)
    elif archetype == "type_c":
        presence = max(presence, 0.52)
    weighted *= 0.22 + 0.78 * presence

    if archetype == "fog_exclude":
        weighted = min(weighted, 0.25)
    elif archetype == "type_a":
        floor = 0.58
        obs_frac = float(observable.get("observable_fraction") or 0) if observable else 0.0
        sector_low = observable.get("sector_cloud_low_mean") if observable else None
        if is_peak and obs_frac >= 0.25:
            floor = max(floor, 0.68)
        elif is_peak and sector_low is not None and float(sector_low) >= 18:
            floor = max(floor, 0.65)
        elif cloud_low >= 70 and rh_850 is not None and rh_850 >= 72:
            floor = max(floor, 0.66)
        weighted = max(weighted, floor)
    elif archetype == "type_b":
        weighted = max(weighted, 0.48)
    elif archetype == "type_c":
        base_floor = 0.52 + float(observable.get("observable_fraction") or 0) * 0.25
        if cloud_low >= 50 and rh_850 is not None and rh_850 >= 72:
            base_floor = max(base_floor, 0.62)
        weighted = max(weighted, base_floor)
    elif is_peak and rh_850 is not None and rh_850 >= 62 and cloud_base < elev_max_5km:
        if elevation > cloud_top or elevation > elev_max_5km - 120:
            weighted = max(weighted, 0.50)
        if rh_850 >= 72 and cloud_base < elev_max_5km - 200:
            obs_frac = float(observable.get("observable_fraction") or 0) if observable else 0.0
            floor = 0.58
            if obs_frac >= 0.22 or cloud_low >= 70:
                floor = 0.68
            weighted = max(weighted, floor)

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

    if is_peak and (precip >= 0.3 or precip_recent >= 2.5 or (precip_recent >= 1.0 and rh >= 88)):
        weighted = min(weighted, 0.38)
    elif is_peak:
        sector_low = observable.get("sector_cloud_low_mean")
        obs_frac = float(observable.get("observable_fraction") or 0)
        if sector_low is not None and float(sector_low) < 6:
            peak_inversion = cloud_low >= 70 and rh_850 is not None and rh_850 >= 72
            if not peak_inversion:
                weighted = min(weighted, 0.44)
        elif sector_low is not None and float(sector_low) >= 22 and obs_frac >= 0.25:
            weighted = max(weighted, 0.58)
        if obs_frac >= 0.35 and sector_low and float(sector_low) >= 15:
            weighted = max(weighted, 0.65)

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
    t_850: float | None = None,
    t_925: float | None = None,
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
