from __future__ import annotations

import asyncio
from datetime import date as date_cls, datetime
from zoneinfo import ZoneInfo

from app.adapters.gibs_wms import fetch_himawari_best_effort
from app.adapters.open_meteo import fetch_elevation, fetch_forecast, parse_daily_astronomy, slice_hourly_window, estimate_cloud_base
from app.adapters.dem import estimate_cloud_top_m, get_terrain_context
from app.engine.observable_field import compute_observable_field
from app.adapters.sector_meteo import (
    build_sector_meteo_index,
    fetch_sector_forecast_multi,
    fetch_sector_historical_multi,
    pick_sector_sample_points,
)
from app.engine.solar import sunrise_azimuth_for_datetime
from app.engine.viewing_mode import resolve_viewing_mode
from app.engine.utils import parse_shanghai_time
from app.services.meteo_cache import (
    astronomy_from_bundle,
    hour_rows_from_hourly,
    is_day_meteo_complete,
    rows_to_hourly,
    serialize_astronomy_for_store,
)
from app.services.cloudsea_store import load_full_day_meteo_rows, load_meteo_day_cache, save_meteo_day_cache
from app.adapters.nsmc_wms import compute_bbox, resolve_bbox_span
from app.engine.cloudsea_features import hour_raw_from_forecast
from app.engine.cloudsea_ml import (
    build_observational_factors,
    get_ml_status,
    merge_ml_cloudsea_score,
    predict_day_cloudsea,
    should_use_spot_ml,
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


def _profile_date_from_astronomy(astronomy: dict[str, dict]) -> date_cls | None:
    if not astronomy:
        return None
    first = sorted(astronomy.keys())[0]
    try:
        return date_cls.fromisoformat(first)
    except ValueError:
        return None


SUNRISE_SECTOR_HOUR_START = 3
SUNRISE_SECTOR_HOUR_END = 7


def _hour_in_sunrise_sector_window(t_str: str) -> bool:
    return SUNRISE_SECTOR_HOUR_START <= parse_shanghai_time(t_str).astimezone(TZ).hour < SUNRISE_SECTOR_HOUR_END


def _needs_sector_meteo(viewing_mode: str, hourly_times: list[str]) -> bool:
    if viewing_mode != "peak_overlook":
        return False
    return any(_hour_in_sunrise_sector_window(t) for t in hourly_times)


def _sector_forecast_days(hourly_times: list[str], *, default: int = 5) -> int:
    if not hourly_times:
        return default
    dates = {parse_shanghai_time(t).date() for t in hourly_times if _hour_in_sunrise_sector_window(t)}
    if not dates:
        return default
    span = (max(dates) - min(dates)).days + 1
    return min(max(span, 1), 16)


async def _ensure_sector_meteo_if_needed(
    *,
    lat: float,
    lng: float,
    terrain: dict,
    viewing_mode: str,
    hourly_times: list[str],
    target_date: date_cls | None = None,
    forecast_days: int = 5,
) -> None:
    """仅 peak_overlook 且 hourly 含日出扇区窗口时才拉扇区气象。"""
    if terrain.get("sector_meteo_by_time"):
        return
    if not _needs_sector_meteo(viewing_mode, hourly_times):
        return
    days = _sector_forecast_days(hourly_times, default=forecast_days)
    terrain["sector_meteo_by_time"] = await _load_sector_meteo_index(
        lat=lat,
        lng=lng,
        terrain=terrain,
        viewing_mode=viewing_mode,
        target_date=target_date,
        forecast_days=days,
    )


async def _load_sector_meteo_index(
    *,
    lat: float,
    lng: float,
    terrain: dict,
    viewing_mode: str,
    target_date: date_cls | None = None,
    forecast_days: int = 5,
) -> dict[str, list[dict]]:
    """峰顶俯瞰：预拉取日出扇区 3–18 km 各 GPS 网格的逐时气象。"""
    if viewing_mode != "peak_overlook":
        return {}
    profile = terrain.get("elev_profile_sunrise")
    az = terrain.get("sunrise_azimuth_deg")
    if not profile or az is None:
        return {}
    sample_points = pick_sector_sample_points(
        profile,
        sunrise_azimuth_deg=float(az),
        visible_range_km=18.0,
    )
    if not sample_points:
        return {}
    if target_date and target_date < datetime.now(TZ).date():
        forecasts = await fetch_sector_historical_multi(
            sample_points,
            start_date=target_date,
            end_date=target_date,
        )
    else:
        forecasts = await fetch_sector_forecast_multi(sample_points, days=forecast_days)
    index = build_sector_meteo_index(forecasts, sample_points)
    terrain["sector_sample_points"] = sample_points
    return index


async def warm_location_caches(
    *,
    lat: float,
    lng: float,
    elevation: float | None = None,
    profile_date: date_cls | None = None,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
) -> None:
    """预热点位 DEM / 海拔缓存，批量回放标注日前调用可显著提速。"""
    profile_day = profile_date or datetime.now(TZ).date()
    elev = elevation
    if elev is None:
        elev = await fetch_elevation(lat, lng)
    await get_terrain_context(
        lat,
        lng,
        elevation=elev,
        profile_date=profile_day,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
    )


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
    terrain: dict | None = None,
    viewing_mode: str = "valley_fill",
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
                    temps=temps,
                    dews=dews,
                )
            )
        return rows

    terrain_ctx: dict = terrain if terrain is not None else {}
    if terrain_ctx and viewing_mode:
        terrain_ctx["viewing_mode"] = viewing_mode
    elev_max_5km = float(terrain_ctx.get("elev_max_5km_m") or elevation)

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
        cloud_top_est = estimate_cloud_top_m(cloud_base, low, mid)
        sun_az = (
            sunrise_azimuth_for_datetime(req.lat, req.lng, sunrise_at)
            if sunrise_at
            else terrain_ctx.get("sunrise_azimuth_deg")
        )
        obs_field = compute_observable_field(
            viewer_elev_m=elevation,
            cloud_base_m=cloud_base,
            cloud_top_m=cloud_top_est,
            visibility_m=vis,
            elev_profile_sunrise=terrain_ctx.get("elev_profile_sunrise"),
            viewing_mode=viewing_mode,
            rh_850=rh_850,
            rh_700=rh_700,
            sunrise_azimuth_deg=sun_az,
            elev_max_5km_m=elev_max_5km,
            sector_meteo=(terrain_ctx.get("sector_meteo_by_time") or {}).get(t_str),
            summit_cloud_low=low,
            summit_rh=rh,
        )
        if 3 <= local_hour < 7:
            prev = terrain_ctx.get("sunrise_observable")
            if prev is None or float(obs_field.get("observable_fraction") or 0) >= float(
                prev.get("observable_fraction") or 0
            ):
                terrain_ctx["sunrise_observable"] = obs_field

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
            viewing_mode=viewing_mode,
            terrain=terrain_ctx or None,
            sunrise_azimuth_deg=sun_az,
            sector_meteo=(terrain_ctx.get("sector_meteo_by_time") or {}).get(t_str),
            summit_cloud_low=low,
            summit_rh=rh,
        )
        if 3 <= local_hour < 7:
            prev_prob = terrain_ctx.get("peak_hour_cloudsea_prob")
            if prev_prob is None or cloudsea.probability >= prev_prob:
                terrain_ctx["peak_hour_observable"] = obs_field
                terrain_ctx["peak_hour_cloudsea_prob"] = cloudsea.probability
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
            viewing_mode=viewing_mode,
            observable=obs_field,
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
            viewing_mode=viewing_mode,
            observable_fraction=float(obs_field.get("observable_fraction") or 0),
            elev_max_5km=elev_max_5km,
            elevation=elevation,
            cloud_base=cloud_base,
        )

        use_ml = (
            should_use_spot_ml(req.spot_id, req.viewpoint_id)
            and 3 <= local_hour < 7
        )
        if use_ml:
            if day_key not in ml_day_cache:
                ml_day_cache[day_key] = predict_day_cloudsea(
                    _sunrise_window_rows(day_key),
                    elevation=elevation,
                    cloud_base_m=cloud_base,
                    spot_id=req.spot_id,
                    viewpoint_id=req.viewpoint_id,
                    terrain=terrain_ctx or None,
                )
            ml_score = ml_day_cache.get(day_key)
            if ml_score is not None:
                cloudsea = merge_ml_cloudsea_score(
                    cloudsea,
                    ml_score,
                    observational=obs,
                    spot_id=req.spot_id,
                    viewpoint_id=req.viewpoint_id,
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
            viewing_mode=viewing_mode,
            terrain=terrain_ctx or None,
            observable=obs_field,
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
    terrain: dict | None = None,
    viewing_mode: str = "valley_fill",
    viewing_mode_note: str = "",
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
        "viewpoint_id": req.viewpoint_id,
        "ml_status": get_ml_status(req.spot_id, req.viewpoint_id),
        "viewing_mode": viewing_mode,
        "viewing_mode_note": viewing_mode_note,
    }
    if terrain:
        location["terrain"] = {
            "elev_max_1km_m": terrain.get("elev_max_1km_m"),
            "elev_max_5km_m": terrain.get("elev_max_5km_m"),
            "relief_5km_m": terrain.get("relief_5km_m"),
            "elev_viewpoint_m": terrain.get("elev_viewpoint_m"),
            "sunrise_azimuth_deg": terrain.get("sunrise_azimuth_deg"),
            "elev_min_sunrise_15km_m": terrain.get("elev_min_sunrise_15km_m"),
        }
        if terrain.get("peak_hour_observable"):
            location["observable"] = terrain["peak_hour_observable"]
        elif terrain.get("sunrise_observable"):
            location["observable"] = terrain["sunrise_observable"]
        if terrain.get("sector_sample_points"):
            location["terrain"]["sector_sample_count"] = len(terrain["sector_sample_points"])
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
    profile_day = now.date()

    forecast, terrain = await asyncio.gather(
        fetch_forecast(req.lat, req.lng, days=5),
        get_terrain_context(
            req.lat,
            req.lng,
            elevation=elevation,
            profile_date=profile_day,
            spot_id=req.spot_id,
            viewpoint_id=req.viewpoint_id,
        ),
    )
    hourly = slice_hourly_window(forecast.get("hourly", {}), days=5)
    astronomy = parse_daily_astronomy(forecast)
    satellite_context = await _fetch_satellite_context(req.lat, req.lng, spot)
    viewing_mode, viewing_mode_note, _ = resolve_viewing_mode(
        spot_id=req.spot_id,
        viewpoint_id=req.viewpoint_id,
        elevation=elevation,
        terrain=terrain,
    )
    terrain["viewing_mode"] = viewing_mode

    await _ensure_sector_meteo_if_needed(
        lat=req.lat,
        lng=req.lng,
        terrain=terrain,
        viewing_mode=viewing_mode,
        hourly_times=hourly.get("time", []),
        forecast_days=5,
    )

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
        terrain=terrain,
        viewing_mode=viewing_mode,
    )
    return _build_response(
        req,
        elevation,
        results,
        astronomy,
        satellite_context,
        terrain=terrain,
        viewing_mode=viewing_mode,
        viewing_mode_note=viewing_mode_note,
    )


async def run_backtest_prediction(
    *,
    req: PredictRequest,
    target_date,
    window_start: int = 3,
    window_end: int = 7,
    prefer_cached_meteo: bool = True,
) -> dict:
    from datetime import date as date_cls

    from app.adapters.open_meteo import fetch_forecast, parse_daily_astronomy, slice_hourly_window
    from app.adapters.open_meteo_historical import (
        fetch_historical_forecast,
        parse_astronomy_for_date,
        slice_hourly_for_date,
    )
    from app.services.cloudsea_store import save_meteo_hourly_batch

    if isinstance(target_date, str):
        target_date = date_cls.fromisoformat(target_date)

    elevation = req.elevation
    if elevation is None:
        elevation = await fetch_elevation(req.lat, req.lng)

    spot = get_spot(req.spot_id) if req.spot_id else None
    cloudsea_months = spot.seasonality.get("cloudsea_months") if spot else None
    sunrise_months = spot.seasonality.get("sunrise_months") if spot else None

    today = datetime.now(TZ).date()
    date_key = target_date.isoformat()
    satellite_context = None
    terrain: dict | None = None
    forecast: dict | None = None

    def _fetch_terrain() -> asyncio.Task:
        return asyncio.create_task(
            get_terrain_context(
                req.lat,
                req.lng,
                elevation=elevation,
                profile_date=target_date,
                spot_id=req.spot_id,
                viewpoint_id=req.viewpoint_id,
            )
        )

    used_cached_meteo = False
    if (
        prefer_cached_meteo
        and target_date < today
        and req.spot_id
        and req.viewpoint_id
    ):
        cached_rows = load_full_day_meteo_rows(req.spot_id, req.viewpoint_id, date_key)
        if is_day_meteo_complete(cached_rows):
            terrain = await _fetch_terrain()
            hourly = rows_to_hourly(cached_rows)
            display_hourly = hourly
            bundle = load_meteo_day_cache(req.spot_id, req.viewpoint_id, date_key)
            astro_bundle = bundle.get("astronomy") if bundle else None
            astronomy = astronomy_from_bundle(astro_bundle, date_key)
            if not astronomy:
                forecast = await fetch_historical_forecast(req.lat, req.lng, target_date, target_date)
                day_astro = parse_astronomy_for_date(forecast, target_date)
                astronomy = {date_key: day_astro} if day_astro else {}
            data_source = "cached_meteo"
            used_cached_meteo = True
            backtest_now = datetime(
                target_date.year,
                target_date.month,
                target_date.day,
                12,
                0,
                tzinfo=TZ,
            )

    if not used_cached_meteo and target_date >= today:
        # 未来/当天：与主页 predict 同源（live forecast），避免 historical API 能见度等字段偏差
        forecast_days = min(max((target_date - today).days + 1, 5), 16)
        terrain_task = _fetch_terrain()
        forecast, terrain = await asyncio.gather(
            fetch_forecast(req.lat, req.lng, days=forecast_days),
            terrain_task,
        )
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
    elif not used_cached_meteo:
        terrain_task = _fetch_terrain()
        forecast, terrain = await asyncio.gather(
            fetch_historical_forecast(req.lat, req.lng, target_date, target_date),
            terrain_task,
        )
        full_hourly = forecast.get("hourly", {})
        hourly = slice_hourly_for_date(full_hourly, target_date)
        display_hourly = hourly
        day_astro = parse_astronomy_for_date(forecast, target_date)
        astronomy = {date_key: day_astro} if day_astro else {}
        data_source = "historical_forecast"
        backtest_now = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            12,
            0,
            tzinfo=TZ,
        )
        if req.spot_id and req.viewpoint_id:
            save_meteo_day_cache(
                spot_id=req.spot_id,
                viewpoint_id=req.viewpoint_id,
                date_key=date_key,
                source="historical_forecast",
                hourly=hourly,
                astronomy=serialize_astronomy_for_store(astronomy),
            )
            save_meteo_hourly_batch(
                spot_id=req.spot_id,
                viewpoint_id=req.viewpoint_id,
                lat=req.lat,
                lng=req.lng,
                elevation=elevation,
                rows=hour_rows_from_hourly(hourly, date_key),
                source="historical_forecast",
            )

    viewing_mode, viewing_mode_note, _ = resolve_viewing_mode(
        spot_id=req.spot_id,
        viewpoint_id=req.viewpoint_id,
        elevation=elevation,
        terrain=terrain,
    )
    terrain["viewing_mode"] = viewing_mode

    await _ensure_sector_meteo_if_needed(
        lat=req.lat,
        lng=req.lng,
        terrain=terrain,
        viewing_mode=viewing_mode,
        hourly_times=display_hourly.get("time", []),
        target_date=target_date,
        forecast_days=min(max((target_date - today).days + 1, 5), 16) if target_date >= today else 1,
    )

    # 标注/回测只需目标日逐时；live forecast 的 hourly 含 5–16 天，全量打分会拖垮 CPU
    results = await asyncio.to_thread(
        build_predictions_from_hourly,
        req=req,
        elevation=elevation,
        hourly=display_hourly,
        astronomy=astronomy,
        cloudsea_months=cloudsea_months,
        sunrise_months=sunrise_months,
        satellite_context=satellite_context,
        now=backtest_now,
        terrain=terrain,
        viewing_mode=viewing_mode,
    )

    window_hours = [
        h
        for h in results
        if window_start <= parse_shanghai_time(h.time).hour < window_end
    ]
    peak = max(window_hours, key=lambda h: h.cloudsea.probability) if window_hours else None

    raw_rows = [
        row
        for row in hour_rows_from_hourly(display_hourly, date_key)
        if window_start <= parse_shanghai_time(str(row["time"])).hour < window_end
    ]

    response = _build_response(
        req,
        elevation,
        results,
        astronomy,
        None,
        terrain=terrain,
        viewing_mode=viewing_mode,
        viewing_mode_note=viewing_mode_note,
    )
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


async def backtest_sunrise_peak(
    *,
    req: PredictRequest,
    target_date: date_cls,
    model_tag: int = 0,
    window_start: int = 3,
    window_end: int = 7,
    prefer_cached_meteo: bool = True,
) -> dict:
    """日出窗口峰值概率（accuracy 等批量场景用，带逐日 Redis 缓存）。"""
    from app.services.cache import cache_get, cache_set

    spot_id = req.spot_id or "_"
    vp_id = req.viewpoint_id or "_"
    date_key = target_date.isoformat()
    cache_key = (
        f"bt_peak:v1:{spot_id}:{vp_id}:{date_key}:{model_tag}:"
        f"{window_start}:{window_end}"
    )
    cached = cache_get(cache_key)
    if cached:
        return dict(cached)

    backtest = await run_backtest_prediction(
        req=req,
        target_date=target_date,
        window_start=window_start,
        window_end=window_end,
        prefer_cached_meteo=prefer_cached_meteo,
    )
    summary = backtest.get("sunrise_window_summary") or {}
    out = {
        "date": date_key,
        "peak_prob": summary.get("max_cloudsea_prob", 0),
        "scenario": summary.get("scenario"),
    }
    today = datetime.now(TZ).date()
    ttl = 600 if target_date >= today else 86400 * 7
    cache_set(cache_key, out, ttl=ttl)
    return out

