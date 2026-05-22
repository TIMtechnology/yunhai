from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.engine.utils import bell_score, clamp, grade_from_probability, parse_shanghai_time, range_score
from app.models.schemas import FactorDetail, PredictionScore

TZ = ZoneInfo("Asia/Shanghai")


def score_sunrise_window(
    *,
    sunrise_at: datetime | None,
    target_time: datetime,
    hourly_times: list[str],
    hourly_low_cloud: list[float | None],
    hourly_mid_cloud: list[float | None],
    hourly_high_cloud: list[float | None],
    hourly_precip: list[float | None],
    hourly_visibility: list[float | None],
    month: int,
    sunrise_best_months: list[int] | None = None,
) -> PredictionScore:
    target_time = target_time.astimezone(TZ)
    sun_time_str = sunrise_at.astimezone(TZ).strftime("%H:%M") if sunrise_at else None

    window_low: list[float] = []
    window_precip = 0.0
    window_vis: list[float] = []
    window_mid_high: list[float] = []

    if sunrise_at:
        for idx, t_str in enumerate(hourly_times):
            t = parse_shanghai_time(t_str)
            if abs((t - sunrise_at).total_seconds()) > 1800:
                continue
            low = hourly_low_cloud[idx] or 0
            mid = hourly_mid_cloud[idx] or 0
            high = hourly_high_cloud[idx] or 0
            precip = hourly_precip[idx] or 0
            vis = hourly_visibility[idx] or 10000
            window_low.append(low)
            window_mid_high.append(mid + high)
            window_precip += precip
            window_vis.append(vis)

    low_cloud_avg = sum(window_low) / len(window_low) if window_low else 50.0
    mid_high_avg = sum(window_mid_high) / len(window_mid_high) if window_mid_high else 50.0
    vis_avg = sum(window_vis) / len(window_vis) if window_vis else 10000

    low_score = bell_score(low_cloud_avg, 15, 25)
    mid_high_score = clamp(1.0 - (mid_high_avg / 100.0))
    precip_score = 1.0 if window_precip <= 0.1 else clamp(1.0 - window_precip)
    vis_score = range_score(vis_avg / 1000.0, 5, 20)
    horizon_score = 0.85
    season_score = 1.0 if sunrise_best_months and month in sunrise_best_months else 0.75

    factors = {
        "low_cloud_window": FactorDetail(
            score=low_score,
            weight=0.35,
            label="日出窗口低云",
            description="日出前后30分钟地平线方向低云越少越好",
            value=f"平均低云 {low_cloud_avg:.0f}%" if window_low else "非日出窗口",
        ),
        "mid_high_cloud": FactorDetail(
            score=mid_high_score,
            weight=0.20,
            label="中高云遮挡",
            description="中高云过厚会挡住朝霞",
            value="中高云偏多" if mid_high_score < 0.5 else "适中",
        ),
        "precipitation": FactorDetail(
            score=precip_score,
            weight=0.15,
            label="降水",
            description="日出时段无降水更适合观测",
            value=f"{window_precip:.1f} mm",
        ),
        "visibility": FactorDetail(
            score=vis_score,
            weight=0.15,
            label="能见度",
            description="能见度好才能看到清晰日出",
            value=f"{vis_avg/1000:.1f} km",
        ),
        "horizon": FactorDetail(
            score=horizon_score,
            weight=0.10,
            label="地平线遮挡",
            description="观景点朝向东方的视野越开阔越好",
            value="开阔",
        ),
        "season": FactorDetail(
            score=season_score,
            weight=0.05,
            label="季节修正",
            description="部分景区在特定季节更适合看日出",
            value=f"{month}月",
        ),
    }

    weighted = sum(f.score * f.weight for f in factors.values())
    probability = int(round(clamp(weighted) * 100))

    return PredictionScore(
        probability=probability,
        grade=grade_from_probability(probability),
        factors=factors,
        sun_time=sun_time_str,
    )
