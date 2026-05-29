from __future__ import annotations

from datetime import date as date_cls, datetime as dt_cls
from typing import Any, Optional

from app.engine.cloudsea_ml import get_ml_status
from app.engine.ml_eligibility import load_sunrise_window_meteo, sunrise_window_rain_summary
from app.models.schemas import PredictRequest
from app.services.cloudsea_store import get_label
from app.services.community_store import (
    COMMUNITY_SPOT_ID,
    community_label_keys,
    get_community_location,
    label_keys_for_location,
)
from app.services.predictor import run_backtest_prediction
from app.services.spot_loader import get_spot, get_viewpoint


def build_predict_request(
    *,
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
    location_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    elevation: Optional[float] = None,
    name: Optional[str] = None,
) -> tuple[PredictRequest, dict[str, Any]]:
    meta: dict[str, Any] = {
        "mode": "curated",
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "location_id": location_id,
    }
    def _community_request(loc_id: str) -> tuple[PredictRequest, dict[str, Any]]:
        loc = get_community_location(loc_id)
        if not loc:
            raise ValueError("社区点位未找到")
        spot_id, viewpoint_id = label_keys_for_location(loc)
        if spot_id == COMMUNITY_SPOT_ID:
            req = PredictRequest(
                lat=loc["lat"],
                lng=loc["lng"],
                elevation=loc.get("elevation"),
                name=loc["name"],
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
                hours=24,
            )
            meta.update(
                {
                    "mode": "community",
                    "location_id": loc_id,
                    "spot_id": spot_id,
                    "viewpoint_id": viewpoint_id,
                    "location_name": loc["name"],
                }
            )
            return req, meta

        spot = get_spot(spot_id)
        vp = get_viewpoint(spot_id, viewpoint_id)
        display = loc["name"]
        if spot and vp:
            display = f"{spot.name} · {vp.name}"
        req = PredictRequest(
            lat=loc["lat"],
            lng=loc["lng"],
            elevation=loc.get("elevation") or (vp.elevation if vp else None),
            name=display,
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
            hours=24,
        )
        meta.update(
            {
                "mode": "curated",
                "location_id": loc_id,
                "spot_id": spot_id,
                "viewpoint_id": viewpoint_id,
                "location_name": loc["name"],
            }
        )
        return req, meta

    if location_id:
        return _community_request(location_id)

    if spot_id == COMMUNITY_SPOT_ID and viewpoint_id:
        return _community_request(viewpoint_id)

    if spot_id and viewpoint_id:
        vp = get_viewpoint(spot_id, viewpoint_id)
        if not vp:
            raise ValueError("观景点未找到")
        spot = get_spot(spot_id)
        req = PredictRequest(
            lat=vp.lat,
            lng=vp.lng,
            elevation=vp.elevation,
            name=f"{spot.name} · {vp.name}" if spot else vp.name,
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
            hours=24,
        )
        meta["mode"] = "curated"
        return req, meta

    if lat is None or lng is None:
        raise ValueError("需提供 spot+viewpoint、location_id 或 lat/lng")

    req = PredictRequest(
        lat=lat,
        lng=lng,
        elevation=elevation,
        name=name or "自定义位置",
        spot_id=None,
        hours=24,
    )
    meta["mode"] = "coordinates"
    return req, meta


async def build_label_session_payload(
    *,
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    window_start: int = 3,
    window_end: int = 7,
    location_id: Optional[str] = None,
    location_name: Optional[str] = None,
) -> dict[str, Any]:
    if location_id:
        req, meta = build_predict_request(location_id=location_id)
    else:
        req, meta = build_predict_request(spot_id=spot_id, viewpoint_id=viewpoint_id)

    target = date_cls.fromisoformat(date_key)
    backtest = await run_backtest_prediction(
        req=req,
        target_date=target,
        window_start=window_start,
        window_end=window_end,
        prefer_cached_meteo=True,
    )
    label = get_label(spot_id, viewpoint_id, date_key, window_start, window_end)
    window_hours = [
        h
        for h in backtest["prediction"]["hours"]
        if str(h["time"]).startswith(date_key)
        and window_start <= dt_cls.fromisoformat(h["time"]).hour < window_end
    ]
    meteo_rows = backtest["raw_meteo"] or load_sunrise_window_meteo(
        spot_id, viewpoint_id, date_key, window_start=window_start, window_end=window_end
    )
    rain_window = sunrise_window_rain_summary(meteo_rows)
    ml_status = get_ml_status(spot_id, viewpoint_id)
    loc = backtest["prediction"].get("location") or {}
    return {
        **meta,
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "date": date_key,
        "label": label,
        "raw_meteo": backtest["raw_meteo"],
        "sunrise_window_summary": backtest["sunrise_window_summary"],
        "hours": window_hours,
        "data_source": backtest["meta"].get("data_source"),
        "location_name": location_name or meta.get("location_name"),
        "lat": req.lat,
        "lng": req.lng,
        "elevation": req.elevation,
        "ml_status": ml_status,
        "rain_window": rain_window,
        "viewing_mode": loc.get("viewing_mode"),
        "viewing_mode_note": loc.get("viewing_mode_note"),
        "observable": loc.get("observable"),
    }
