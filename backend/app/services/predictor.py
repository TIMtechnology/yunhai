from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.adapters.gibs_wms import fetch_himawari_best_effort
from app.adapters.open_meteo import fetch_forecast, parse_daily_astronomy, slice_hourly_window
from app.engine.utils import parse_shanghai_time
from app.adapters.nsmc_wms import compute_bbox, resolve_bbox_span
from app.engine.cloudsea_scorer import score_cloudsea
from app.engine.satellite_analyzer import analyze_ir_image
from app.engine.scenario import build_scenario, weather_text
from app.engine.sunrise_scorer import score_sunrise_window
from app.models.schemas import (
    BestWindow,
    DaySummary,
    HourPrediction,
    PredictRequest,
    PredictResponse,
    ScenarioPrediction,
    WeatherSnapshot,
)
from app.services.spot_loader import get_spot

TZ = ZoneInfo("Asia/Shanghai")
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _recent_precip(precips: list, idx: int, hours: int = 48) -> float:
    start = max(0, idx - hours)
    return sum(p or 0 for p in precips[start : idx + 1])


def _find_best_windows(hours: list[HourPrediction], key: str, threshold: int = 60) -> list[BestWindow]:
    windows: list[BestWindow] = []
    current_start = None
    peak = 0

    for item in hours:
        prob = getattr(item, key).probability
        if prob >= threshold:
            if current_start is None:
                current_start = item.time
                peak = prob
            else:
                peak = max(peak, prob)
        elif current_start is not None:
            windows.append(BestWindow(start=current_start, end=item.time, peak_prob=peak))
            current_start = None
            peak = 0

    if current_start is not None and hours:
        windows.append(BestWindow(start=current_start, end=hours[-1].time, peak_prob=peak))
    return windows[:5]


def _is_near_sunrise(target: datetime, sunrise_at: datetime | None) -> bool:
    if sunrise_at is None:
        return 4 <= target.astimezone(TZ).hour <= 7
    return abs((target.astimezone(TZ) - sunrise_at.astimezone(TZ)).total_seconds()) <= 2700


def _closest_hour_index(entries: list[tuple[int, HourPrediction]], moment: datetime) -> tuple[int, HourPrediction] | None:
    if not entries:
        return None
    return min(
        entries,
        key=lambda x: abs(
            (parse_shanghai_time(x[1].time) - moment.astimezone(TZ)).total_seconds()
        ),
    )


def _build_day_summaries(
    hours: list[HourPrediction],
    astronomy: dict[str, dict[str, datetime]],
) -> list[DaySummary]:
    by_date: dict[str, list[tuple[int, HourPrediction]]] = {}
    for idx, item in enumerate(hours):
        day = item.time[:10]
        by_date.setdefault(day, []).append((idx, item))

    summaries: list[DaySummary] = []
    for date_key in sorted(by_date.keys()):
        entries = by_date[date_key]
        dt = parse_shanghai_time(entries[0][1].time)
        weekday = WEEKDAYS[dt.weekday()]

        sunrise_entry = None
        astro = astronomy.get(date_key, {})
        sunrise_at = astro.get("sunrise")
        if sunrise_at:
            sunrise_entry = _closest_hour_index(entries, sunrise_at)
        if sunrise_entry is None:
            morning = [
                (i, h)
                for i, h in entries
                if 4 <= parse_shanghai_time(h.time).hour <= 8
            ]
            if morning:
                sunrise_entry = max(
                    morning,
                    key=lambda x: (
                        x[1].scenario.combined_score,
                        x[1].cloudsea.probability + x[1].sunrise.probability,
                    ),
                )

        peak = max(entries, key=lambda x: x[1].cloudsea.probability)
        recommend: list[str] = []
        for idx, item in entries:
            if item.scenario.level <= 2 and item.scenario.combined_score >= 60:
                t = parse_shanghai_time(item.time)
                recommend.append(f"{t.strftime('%H:%M')} {item.scenario.label}")

        sunrise_idx, sunrise_item = sunrise_entry if sunrise_entry else (None, None)
        sunrise_time = (
            sunrise_at.astimezone(TZ).strftime("%H:%M")
            if sunrise_at
            else (sunrise_item.sunrise.sun_time if sunrise_item else None)
        )
        summaries.append(
            DaySummary(
                date=date_key,
                weekday=weekday,
                sunrise_time=sunrise_time,
                sunrise_hour_index=sunrise_idx,
                peak_cloudsea_prob=peak[1].cloudsea.probability,
                peak_cloudsea_time=parse_shanghai_time(peak[1].time).strftime("%H:%M"),
                sunrise_scenario_label=sunrise_item.scenario.label if sunrise_item else None,
                sunrise_combined_score=sunrise_item.scenario.combined_score if sunrise_item else 0,
                recommend_periods=recommend[:4],
            )
        )
    return summaries


async def _fetch_satellite_context(lat: float, lng: float, spot) -> dict | None:
    region = spot.cloud_region.model_dump() if spot and spot.cloud_region else None
    lng_span, lat_span = resolve_bbox_span(None, None, region)
    bbox = compute_bbox(lat, lng, lng_span, lat_span)
    now = datetime.now(TZ)
    try:
        result = await fetch_himawari_best_effort(bbox, now, lookback_hours=12)
    except Exception:
        return None
    if not result:
        return None
    analysis = analyze_ir_image(result["content"])
    return {
        **analysis,
        "datetime_utc": result["datetime_utc"],
        "lookback_hours": result.get("lookback_hours") or 0,
        "source": result.get("source", "gibs_himawari_b13"),
    }


async def run_prediction(req: PredictRequest) -> PredictResponse:
    elevation = req.elevation
    if elevation is None:
        elevation = await fetch_elevation(req.lat, req.lng)

    spot = get_spot(req.spot_id) if req.spot_id else None
    cloudsea_months = spot.seasonality.get("cloudsea_months") if spot else None
    sunrise_months = spot.seasonality.get("sunrise_months") if spot else None
    satellite_context = await _fetch_satellite_context(req.lat, req.lng, spot)

    forecast = await fetch_forecast(req.lat, req.lng, days=5)
    hourly = slice_hourly_window(forecast.get("hourly", {}), days=5)
    astronomy = parse_daily_astronomy(forecast)
    times: list[str] = hourly.get("time", [])[: req.hours]

    temps = hourly.get("temperature_2m", [])
    rhs = hourly.get("relative_humidity_2m", [])
    dews = hourly.get("dew_point_2m", [])
    precips = hourly.get("precipitation", [])
    cloud_total = hourly.get("cloud_cover", [])
    cloud_low = hourly.get("cloud_cover_low", [])
    cloud_mid = hourly.get("cloud_cover_mid", [])
    cloud_high = hourly.get("cloud_cover_high", [])
    winds = hourly.get("wind_speed_10m", [])
    visibilities = hourly.get("visibility", [])

    results: list[HourPrediction] = []
    for idx, t_str in enumerate(times):
        t = parse_shanghai_time(t_str)
        month = t.month
        rh = rhs[idx] if idx < len(rhs) and rhs[idx] is not None else 70
        temp = temps[idx] if idx < len(temps) and temps[idx] is not None else 15
        dew = dews[idx] if idx < len(dews) and dews[idx] is not None else temp - 5
        precip = precips[idx] if idx < len(precips) and precips[idx] is not None else 0
        low = cloud_low[idx] if idx < len(cloud_low) and cloud_low[idx] is not None else 50
        mid = cloud_mid[idx] if idx < len(cloud_mid) and cloud_mid[idx] is not None else 40
        high = cloud_high[idx] if idx < len(cloud_high) and cloud_high[idx] is not None else 30
        total = cloud_total[idx] if idx < len(cloud_total) and cloud_total[idx] is not None else (low + mid + high) / 3
        wind = winds[idx] if idx < len(winds) and winds[idx] is not None else 3
        vis = visibilities[idx] if idx < len(visibilities) and visibilities[idx] is not None else None
        recent = _recent_precip(precips, idx)

        day_key = t_str[:10]
        sunrise_at = astronomy.get(day_key, {}).get("sunrise")

        cloudsea = score_cloudsea(
            rh=rh,
            cloud_low=low,
            cloud_mid=mid,
            cloud_high=high,
            wind=wind,
            precip=precip,
            precip_recent=recent,
            temp=temp,
            dewpoint=dew,
            elevation=elevation,
            month=month,
            cloudsea_months=cloudsea_months,
            satellite_context=satellite_context,
        )

        sunrise = score_sunrise_window(
            sunrise_at=sunrise_at,
            target_time=t,
            hourly_times=times,
            hourly_low_cloud=cloud_low,
            hourly_mid_cloud=cloud_mid,
            hourly_high_cloud=cloud_high,
            hourly_precip=precips,
            hourly_visibility=visibilities,
            month=month,
            sunrise_best_months=sunrise_months,
        )

        w_text = weather_text(precip=precip, cloud_low=low, cloud_mid=mid, cloud_high=high, rh=rh)
        near_sun = _is_near_sunrise(t, sunrise_at)
        scenario_data = build_scenario(
            cloudsea_prob=cloudsea.probability,
            sunrise_prob=sunrise.probability,
            precip=precip,
            cloud_low=low,
            cloud_mid=mid,
            cloud_high=high,
            is_sunrise_window=near_sun,
        )

        results.append(
            HourPrediction(
                time=t_str,
                cloudsea=cloudsea,
                sunrise=sunrise,
                weather=WeatherSnapshot(
                    temperature=round(temp, 1),
                    humidity=round(rh, 0),
                    precipitation=round(precip, 1),
                    cloud_cover=round(total, 0),
                    cloud_cover_low=round(low, 0),
                    cloud_cover_mid=round(mid, 0),
                    cloud_cover_high=round(high, 0),
                    wind_speed=round(wind, 1),
                    visibility=round(vis, 0) if vis is not None else None,
                    weather_text=w_text,
                ),
                scenario=ScenarioPrediction(**scenario_data),
                is_sunrise_window=near_sun,
            )
        )

    days = _build_day_summaries(results, astronomy)

    sunrise_days: list[dict] = []
    for day in days:
        if day.sunrise_hour_index is not None:
            item = results[day.sunrise_hour_index]
            sunrise_days.append(
                {
                    "date": day.date,
                    "prob": item.sunrise.probability,
                    "combined": item.scenario.combined_score,
                    "sun_time": day.sunrise_time,
                    "grade": item.sunrise.grade,
                    "scenario": item.scenario.label,
                }
            )

    location = {
        "lat": req.lat,
        "lng": req.lng,
        "elevation": round(elevation, 1),
        "name": req.name,
        "spot_id": req.spot_id,
    }
    if satellite_context:
        location["satellite_context"] = satellite_context

    return PredictResponse(
        location=location,
        hours=results,
        days=days,
        best_windows={
            "cloudsea": _find_best_windows(results, "cloudsea"),
            "sunrise": sunrise_days,
        },
    )
