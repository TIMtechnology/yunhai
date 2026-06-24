from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.adapters.gibs_wms import fetch_himawari_best_effort
from app.adapters.nsmc_wms import (
    clamp_to_latest_satellite_time,
    compute_bbox,
    normalize_bbox,
    parse_time,
    resolve_bbox_span,
    satellite_time_available,
)
from app.engine.satellite_analyzer import analyze_ir_image
from app.adapters.open_meteo import fetch_elevation, fetch_forecast
from app.adapters.tianditu_poi import search_poi
from app.adapters.dem import get_terrain_context
from app.models.schemas import PredictRequest, PredictResponse, TerrainContextResponse
from app.services.meteo_profile import build_meteo_profile
from app.services.predictor import run_prediction
from app.services.spot_loader import get_spot, get_viewpoint, search_spots

router = APIRouter(prefix="/api", tags=["api"])


def _predict_log_context(
    request: Request,
    *,
    page_source: str | None = None,
) -> dict[str, str | None]:
    referer = request.headers.get("referer") or ""
    src = page_source
    if not src:
        if "label" in referer.lower():
            src = "label"
        elif referer:
            src = "main"
        else:
            src = "api"
    return {
        "page_source": src,
        "client_id": request.headers.get("x-contributor-id") or request.cookies.get("yunhai_contributor_id"),
    }


@router.get("/spots/search")
async def spots_search(
    q: str = "",
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    curated_only: bool = True,
    poi_only: bool = False,
):
    """精选景区搜索。POI 由前端浏览器直连高德（Key 为浏览器端权限）。"""
    if poi_only:
        return {"results": []}

    curated = search_spots(q)
    if curated_only:
        return {"results": curated}

    poi = await search_poi(q, count=12, center_lat=lat, center_lng=lng) if q.strip() else []
    return {"results": curated + poi}


@router.get("/spots/{spot_id}")
async def spot_detail(spot_id: str):
    spot = get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="景区未找到")
    return spot


@router.get("/spots/{spot_id}/viewpoints/{viewpoint_id}")
async def viewpoint_detail(spot_id: str, viewpoint_id: str):
    vp = get_viewpoint(spot_id, viewpoint_id)
    if not vp:
        raise HTTPException(status_code=404, detail="观景点未找到")
    spot = get_spot(spot_id)
    return {"spot": spot, "viewpoint": vp}


@router.post("/predict", response_model=PredictResponse)
async def predict(body: PredictRequest, request: Request):
    ctx = _predict_log_context(request)
    return await run_prediction(body, **ctx)


@router.get("/predict/{spot_id}/viewpoint/{viewpoint_id}", response_model=PredictResponse)
async def predict_viewpoint(spot_id: str, viewpoint_id: str, request: Request, hours: int = 120):
    vp = get_viewpoint(spot_id, viewpoint_id)
    if not vp:
        raise HTTPException(status_code=404, detail="观景点未找到")
    spot = get_spot(spot_id)
    req = PredictRequest(
        lat=vp.lat,
        lng=vp.lng,
        elevation=vp.elevation,
        name=f"{spot.name} · {vp.name}" if spot else vp.name,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        hours=hours,
        coord_sys=spot.coord_sys if spot else "GCJ-02",
    )
    ctx = _predict_log_context(request)
    return await run_prediction(req, **ctx)


@router.get("/weather/raw")
async def weather_raw(lat: float, lng: float):
    forecast = await fetch_forecast(lat, lng)
    elevation = await fetch_elevation(lat, lng)
    return {"forecast": forecast, "elevation": elevation}


@router.get("/meteo/profile")
async def meteo_profile(lat: float, lng: float, date: str, elevation: Optional[float] = None):
    try:
        return await build_meteo_profile(lat=lat, lng=lng, date_key=date, elevation=elevation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date 格式须为 YYYY-MM-DD") from exc


@router.get("/terrain/context", response_model=TerrainContextResponse)
async def terrain_context(
    lat: float,
    lng: float,
    elevation: Optional[float] = None,
    cloud_base_m: Optional[float] = None,
    cloud_top_m: Optional[float] = None,
    cloud_low_pct: Optional[float] = None,
    cloud_mid_pct: Optional[float] = None,
    temp_c: Optional[float] = None,
    dewpoint_c: Optional[float] = None,
    visibility_m: Optional[float] = None,
    profile_date: Optional[str] = None,
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
):
    """DEM v0：周边地形统计 + 日出方向剖面 + 观云模式 + 可选云高–地形相对位置。"""
    from datetime import date as date_cls

    parsed_date = None
    if profile_date:
        try:
            parsed_date = date_cls.fromisoformat(profile_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="profile_date 格式须为 YYYY-MM-DD") from exc
    return await get_terrain_context(
        lat,
        lng,
        elevation=elevation,
        cloud_base_m=cloud_base_m,
        cloud_top_m=cloud_top_m,
        cloud_low_pct=cloud_low_pct,
        cloud_mid_pct=cloud_mid_pct,
        temp_c=temp_c,
        dewpoint_c=dewpoint_c,
        visibility_m=visibility_m,
        profile_date=parsed_date,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
    )


@router.get("/satellite/cloud")
async def satellite_cloud(
    lat: float,
    lng: float,
    time: str,
    spot_id: Optional[str] = None,
    span_lng: Optional[float] = None,
    span_lat: Optional[float] = None,
    west: Optional[float] = None,
    south: Optional[float] = None,
    east: Optional[float] = None,
    north: Optional[float] = None,
):
    """按地图视口裁切 Himawari 红外云图（NASA GIBS）。"""
    try:
        dt = parse_time(time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="time 格式无效，需 ISO8601") from exc

    spot = get_spot(spot_id) if spot_id else None
    region = spot.cloud_region.model_dump() if spot and spot.cloud_region else None
    lng_span, lat_span = resolve_bbox_span(span_lng, span_lat, region)

    if all(v is not None for v in (west, south, east, north)):
        bbox = normalize_bbox({"west": west, "south": south, "east": east, "north": north})
    else:
        bbox = compute_bbox(lat, lng, lng_span, lat_span)

    effective = clamp_to_latest_satellite_time(dt)
    if not satellite_time_available(effective):
        return {
            "bounds": bbox,
            "image_base64": "",
            "datetime_utc": "",
            "source": "gibs_himawari_b13",
            "fallback": True,
            "reason": "time_unavailable",
            "span_lng": (bbox["east"] - bbox["west"]) / 2,
            "span_lat": (bbox["north"] - bbox["south"]) / 2,
            "lookback_hours": 0,
        }

    try:
        result = await fetch_himawari_best_effort(bbox, effective)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"卫星云图获取失败: {exc}") from exc

    if not result:
        return {
            "bounds": bbox,
            "image_base64": "",
            "datetime_utc": "",
            "source": "gibs_himawari_b13",
            "fallback": True,
            "reason": "empty_image",
            "span_lng": (bbox["east"] - bbox["west"]) / 2,
            "span_lat": (bbox["north"] - bbox["south"]) / 2,
            "lookback_hours": 0,
        }

    encoded = base64.b64encode(result["content"]).decode("ascii")
    lookback = int(result.get("lookback_hours") or 0)
    analysis = analyze_ir_image(result["content"])
    return {
        "bounds": result["bounds"],
        "image_base64": encoded,
        "datetime_utc": result["datetime_utc"],
        "source": result.get("source", "gibs_himawari_b13"),
        "fallback": False,
        "reason": "lookback" if lookback > 0 else None,
        "span_lng": result["span_lng"],
        "span_lat": result["span_lat"],
        "lookback_hours": lookback,
        "analysis": analysis,
    }


@router.get("/prediction-feedback/history")
async def prediction_feedback_history(
    spot_id: str,
    viewpoint_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """标注页只读：某日历史预测访问与 forecast vs 实况摘要。"""
    from app.services.prediction_feedback import get_prediction_history

    return get_prediction_history(spot_id, viewpoint_id, date)


@router.get("/prediction-feedback/history/{access_log_id}")
async def prediction_feedback_history_detail(access_log_id: int):
    """标注页只读：单次访问快照的双轨曲线与分段差异。"""
    from app.services.prediction_feedback import get_prediction_snapshot_detail

    row = get_prediction_snapshot_detail(access_log_id)
    if not row:
        raise HTTPException(status_code=404, detail="访问记录未找到")
    return row
