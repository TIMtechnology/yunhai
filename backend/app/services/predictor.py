from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.adapters.gibs_wms import fetch_himawari_best_effort
from app.adapters.open_meteo import fetch_elevation, fetch_forecast, parse_daily_astronomy, slice_hourly_window, estimate_cloud_base
from app.engine.utils import parse_shanghai_time
from app.adapters.nsmc_wms import compute_bbox, resolve_bbox_span
from app.engine.cloudsea_features import hour_raw_from_forecast
from app.engine.cloudsea_ml import (
    build_observational_factors,
    merge_ml_cloudsea_score,
    ml_enabled,
    predict_day_cloudsea,
)
from app.engine.cloudsea_scorer import _classify_cloudsea_archetype, cloudsea_plausibility_cap, score_cloudsea
from app.engine.satellite_analyzer import analyze_ir_image
from app.engine.scenario import build_scenario, weather_text
from app.engine.sunrise_scorer import score_sunrise_window
from app.models.schemas import (
    BestWindow,
    DaySummary,
    HourPrediction,
    PredictRequest,
    PredictResponse,
    PredictionScore,
    ScenarioPrediction,
    WeatherSnapshot,
)
from app.services.cache import cache_get, cache_set
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


def _satellite_context_for_hour(
    ctx: dict | None,
    target: datetime,
    now: datetime,
) -> dict | None:
    """Himawari 为近实况，仅用于当天已发生时段的预报校正，不参与未来日期评分。"""
    if not ctx:
        return None
    target_local = target.astimezone(TZ)
    now_local = now.astimezone(TZ)
    if target_local.date() != now_local.date():
        return None
    target_hour = target_local.replace(minute=0, second=0, microsecond=0)
    now_hour = now_local.replace(minute=0, second=0, microsecond=0)
    if target_hour > now_hour:
        return None
    return ctx


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
    cache_key = (
        f"sat_ctx:{bbox['west']:.3f}:{bbox['south']:.3f}:"
        f"{bbox['east']:.3f}:{bbox['north']:.3f}:{now.strftime('%Y%m%d%H')}"
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return cached if cached else None

    try:
        result = await fetch_himawari_best_effort(bbox, now, lookback_hours=12)
    except Exception:
        cache_set(cache_key, {}, ttl=300)
        return None
    if not result:
        cache_set(cache_key, {}, ttl=300)
        return None
    analysis = analyze_ir_image(result["content"])
    payload = {
        **analysis,
        "datetime_utc": result["datetime_utc"],
        "lookback_hours": result.get("lookback_hours") or 0,
        "source": result.get("source", "gibs_himawari_b13"),
    }
    cache_set(cache_key, payload)
    return payload


def build_predictions_from_hourly(
    *,
    req: PredictRequest,
    elevation: float,
    hourly: dict,
    astronomy: dict[str, dict[str, datetime]],
    cloudsea_months: list[int] | None,
    sunrise_months: list[int] | None,
    satellite_context: dict | None,
    now: datetime,
    hour_limit: int | None = None,
) -> list[HourPrediction]:
    times: list[str] = hourly.get("time", [])
    if hour_limit is not None:
        times = times[:hour_limit]

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
    rh_850_series = hourly.get("relative_humidity_850hPa", [])
    rh_700_series = hourly.get("relative_humidity_700hPa", [])
    t_850_series = hourly.get("temperature_850hPa", [])
    t_925_series = hourly.get("temperature_925hPa", [])

    ml_day_cache: dict[str, PredictionScore | None] = {}

    def _sunrise_window_rows(day_key: str) -> list[dict]:
        rows: list[dict] = []
        for j, ts in enumerate(times):
            local = parse_shanghai_time(ts).astimezone(TZ)
            if ts[:10] != day_key or local.hour < 3 or local.hour >= 7:
                continue
            rows.append(
                hour_raw_from_forecast(
                    t_str=ts,
                    idx=j,
                    cloud_low=cloud_low,
                    cloud_mid=cloud_mid,
                    cloud_high=cloud_high,
                    visibilities=visibilities,
                    rhs=rhs,
                    rh_850_series=rh_850_series,
                    rh_700_series=rh_700_series,
                    t_850_series=t_850_series,
                    t_925_series=t_925_series,
                    winds=winds,
                    precips=precips,
                )
            )
        return rows

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

        rh_850 = rh_850_series[idx] if idx < len(rh_850_series) else None
        rh_700 = rh_700_series[idx] if idx < len(rh_700_series) else None
        t_850 = t_850_series[idx] if idx < len(t_850_series) else None
        t_925 = t_925_series[idx] if idx < len(t_925_series) else None

        day_key = t_str[:10]
        sunrise_at = astronomy.get(day_key, {}).get("sunrise")
        local_hour = t.astimezone(TZ).hour

        cloud_base = estimate_cloud_base(temp, dew)
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
            satellite_context=_satellite_context_for_hour(satellite_context, t, now),
            rh_850=rh_850,
            rh_700=rh_700,
            t_850=t_850,
            t_925=t_925,
            visibility=vis,
        )
        obs = build_observational_factors(
            cloud_low=low,
            cloud_mid=mid,
            cloud_high=high,
            visibility=vis,
            rh=rh,
            rh_850=rh_850,
            rh_700=rh_700,
            t_850=t_850,
            t_925=t_925,
            wind=wind,
            precip_recent=recent,
            elevation=elevation,
        )
        archetype, _ = _classify_cloudsea_archetype(
            cloud_low=low,
            cloud_mid=mid,
            visibility=vis,
            rh=rh,
            rh_850=rh_850,
            precip_recent=recent,
            t_850=t_850,
            t_925=t_925,
        )
        plausibility_cap = cloudsea_plausibility_cap(
            cloud_low=low,
            cloud_mid=mid,
            rh=rh,
            rh_850=rh_850,
            rh_700=rh_700,
            t_850=t_850,
            t_925=t_925,
            visibility=vis,
            archetype=archetype,
        )

        use_ml = ml_enabled() and 3 <= local_hour < 7
        if use_ml:
            if day_key not in ml_day_cache:
                ml_day_cache[day_key] = predict_day_cloudsea(
                    _sunrise_window_rows(day_key),
                    elevation=elevation,
                    cloud_base_m=cloud_base,
                )
            ml_score = ml_day_cache.get(day_key)
            if ml_score is not None:
                cloudsea = merge_ml_cloudsea_score(
                    cloudsea,
                    ml_score,
                    observational=obs,
                    spot_id=req.spot_id,
                    plausibility_cap=plausibility_cap,
                )

        if not (use_ml and ml_day_cache.get(day_key) is not None):
            cloudsea = PredictionScore(
                probability=cloudsea.probability,
                grade=cloudsea.grade,
                factors={**cloudsea.factors, **{f"obs_{k}": v for k, v in obs.items()}},
                cloud_base_m=cloudsea.cloud_base_m,
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
            visibility=vis,
            elevation=elevation,
            rh=rh,
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
    return results


def _build_response(
    req: PredictRequest,
    elevation: float,
    results: list[HourPrediction],
    astronomy: dict[str, dict[str, datetime]],
    satellite_context: dict | None,
) -> PredictResponse:
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


async def run_prediction(req: PredictRequest) -> PredictResponse:
    elevation = req.elevation
    if elevation is None:
        elevation = await fetch_elevation(req.lat, req.lng)

    spot = get_spot(req.spot_id) if req.spot_id else None
    cloudsea_months = spot.seasonality.get("cloudsea_months") if spot else None
    sunrise_months = spot.seasonality.get("sunrise_months") if spot else None
    now = datetime.now(TZ)
    satellite_context = await _fetch_satellite_context(req.lat, req.lng, spot)

    forecast = await fetch_forecast(req.lat, req.lng, days=5)
    hourly = slice_hourly_window(forecast.get("hourly", {}), days=5)
    astronomy = parse_daily_astronomy(forecast)

    results = build_predictions_from_hourly(
        req=req,
        elevation=elevation,
        hourly=hourly,
        astronomy=astronomy,
        cloudsea_months=cloudsea_months,
        sunrise_months=sunrise_months,
        satellite_context=satellite_context,
        now=now,
        hour_limit=req.hours,
    )
    return _build_response(req, elevation, results, astronomy, satellite_context)


async def run_backtest_prediction(
    *,
    req: PredictRequest,
    target_date,
    window_start: int = 3,
    window_end: int = 7,
) -> dict:
    from datetime import date as date_cls

    from app.adapters.open_meteo import fetch_forecast, parse_daily_astronomy, slice_hourly_window
    from app.adapters.open_meteo_historical import (
        fetch_historical_forecast,
        parse_astronomy_for_date,
        slice_hourly_for_date,
    )

    if isinstance(target_date, str):
        target_date = date_cls.fromisoformat(target_date)

    elevation = req.elevation
    if elevation is None:
        elevation = await fetch_elevation(req.lat, req.lng)

    spot = get_spot(req.spot_id) if req.spot_id else None
    cloudsea_months = spot.seasonality.get("cloudsea_months") if spot else None
    sunrise_months = spot.seasonality.get("sunrise_months") if spot else None

    today = datetime.now(TZ).date()
    satellite_context = None
    if target_date >= today:
        # 未来/当天：与主页 predict 同源（live forecast），避免 historical API 能见度等字段偏差
        forecast_days = min(max((target_date - today).days + 1, 5), 16)
        forecast = await fetch_forecast(req.lat, req.lng, days=forecast_days)
        hourly = slice_hourly_window(forecast.get("hourly", {}), days=forecast_days)
        display_hourly = slice_hourly_for_date(hourly, target_date)
        astronomy = parse_daily_astronomy(forecast)
        data_source = "live_forecast"
        if target_date == today:
            satellite_context = await _fetch_satellite_context(req.lat, req.lng, spot)
            backtest_now = datetime.now(TZ)
        else:
            backtest_now = datetime(
                target_date.year,
                target_date.month,
                target_date.day,
                12,
                0,
                tzinfo=TZ,
            )
    else:
        forecast = await fetch_historical_forecast(req.lat, req.lng, target_date, target_date)
        full_hourly = forecast.get("hourly", {})
        hourly = slice_hourly_for_date(full_hourly, target_date)
        display_hourly = hourly
        day_astro = parse_astronomy_for_date(forecast, target_date)
        astronomy = {target_date.isoformat(): day_astro} if day_astro else {}
        data_source = "historical_forecast"
        backtest_now = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            12,
            0,
            tzinfo=TZ,
        )

    results = build_predictions_from_hourly(
        req=req,
        elevation=elevation,
        hourly=hourly,
        astronomy=astronomy,
        cloudsea_months=cloudsea_months,
        sunrise_months=sunrise_months,
        satellite_context=satellite_context,
        now=backtest_now,
    )

    window_hours = [
        h
        for h in results
        if h.time.startswith(target_date.isoformat())
        and window_start <= parse_shanghai_time(h.time).hour < window_end
    ]
    peak = max(window_hours, key=lambda h: h.cloudsea.probability) if window_hours else None

    raw_rows = []
    times = display_hourly.get("time", [])
    precips = hourly.get("precipitation", [])
    time_to_idx = {t: i for i, t in enumerate(hourly.get("time", []))}
    for idx, t_str in enumerate(times):
        hour = parse_shanghai_time(t_str).hour
        if hour < window_start or hour >= window_end:
            continue
        src_idx = time_to_idx.get(t_str, idx)
        raw_rows.append(
            {
                "time": t_str,
                "cloud_low": display_hourly.get("cloud_cover_low", [None])[idx],
                "cloud_mid": display_hourly.get("cloud_cover_mid", [None])[idx],
                "cloud_high": display_hourly.get("cloud_cover_high", [None])[idx],
                "visibility": display_hourly.get("visibility", [None])[idx],
                "rh": display_hourly.get("relative_humidity_2m", [None])[idx],
                "rh_850": display_hourly.get("relative_humidity_850hPa", [None])[idx],
                "rh_700": display_hourly.get("relative_humidity_700hPa", [None])[idx],
                "t_850": display_hourly.get("temperature_850hPa", [None])[idx],
                "t_925": display_hourly.get("temperature_925hPa", [None])[idx],
                "inversion": (
                    display_hourly.get("temperature_850hPa", [None])[idx]
                    - display_hourly.get("temperature_925hPa", [None])[idx]
                    if idx < len(display_hourly.get("temperature_850hPa", []))
                    and idx < len(display_hourly.get("temperature_925hPa", []))
                    and display_hourly.get("temperature_850hPa", [None])[idx] is not None
                    and display_hourly.get("temperature_925hPa", [None])[idx] is not None
                    else None
                ),
                "wind": display_hourly.get("wind_speed_10m", [None])[idx],
                "precip48": _recent_precip(precips, src_idx),
            }
        )

    response = _build_response(req, elevation, results, astronomy, None)
    summary = None
    if peak:
        ph = parse_shanghai_time(peak.time).hour
        feat = next((r for r in raw_rows if r["time"] == peak.time), {})
        summary = {
            "peak_time": peak.time,
            "peak_hour": ph,
            "max_cloudsea_prob": peak.cloudsea.probability,
            "scenario": peak.scenario.label,
            "features_at_peak": feat,
        }

    return {
        "meta": {
            "date": target_date.isoformat(),
            "data_source": data_source,
            "model": "fuzzy_v2_archetype",
            "window_start": window_start,
            "window_end": window_end,
        },
        "raw_meteo": raw_rows,
        "sunrise_window_summary": summary,
        "prediction": response.model_dump(),
    }

