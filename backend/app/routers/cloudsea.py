from __future__ import annotations

from datetime import date as date_cls, datetime as dt_cls
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.models.schemas import PredictRequest
from app.services.cloudsea_store import (
    calendar_summary,
    get_label,
    init_store,
    list_labels,
    save_meteo_hourly,
    save_prediction_run,
    upsert_label,
)
from app.services.predictor import run_backtest_prediction
from app.services.spot_loader import get_spot, get_viewpoint

router = APIRouter(tags=["cloudsea"])

DASHBOARD_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
}


class LabelBody(BaseModel):
    spot_id: str
    viewpoint_id: str
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: str = Field(..., pattern=r"^(none|partial|full)$")
    window_start: int = 3
    window_end: int = 7
    confidence: str = "confirmed"
    notes: str = ""


def _require_cloudsea_enabled() -> None:
    if not settings.cloudsea_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _admin_token() -> str:
    return settings.cloudsea_admin_token or settings.analytics_admin_token


def verify_cloudsea_token(x_cloudsea_token: str = Header(default="")) -> None:
    _require_cloudsea_enabled()
    token = _admin_token()
    if not token or x_cloudsea_token != token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _cors_headers(origin: str | None) -> dict[str, str]:
    if origin and (origin in DASHBOARD_ORIGINS or origin.endswith("yunhai.timkj.com")):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "X-Cloudsea-Token, Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        }
    return {}


def _resolve_location(
    spot_id: Optional[str],
    viewpoint_id: Optional[str],
    lat: Optional[float],
    lng: Optional[float],
    elevation: Optional[float],
    name: Optional[str],
) -> PredictRequest:
    if spot_id and viewpoint_id:
        vp = get_viewpoint(spot_id, viewpoint_id)
        if not vp:
            raise HTTPException(status_code=404, detail="观景点未找到")
        spot = get_spot(spot_id)
        return PredictRequest(
            lat=vp.lat,
            lng=vp.lng,
            elevation=vp.elevation,
            name=f"{spot.name} · {vp.name}" if spot else vp.name,
            spot_id=spot_id,
            hours=24,
        )
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="需提供 spot_id+viewpoint_id 或 lat+lng")
    return PredictRequest(
        lat=lat,
        lng=lng,
        elevation=elevation,
        name=name or "自定义位置",
        spot_id=spot_id,
        hours=24,
    )


@router.options("/api/internal/cloudsea/{path:path}")
async def cloudsea_options(path: str, response: Response, origin: str | None = Header(default=None)):
    for k, v in _cors_headers(origin).items():
        response.headers[k] = v
    return {}


@router.get("/api/internal/backtest/predict")
async def backtest_predict(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    elevation: Optional[float] = None,
    window_start: int = 3,
    window_end: int = 7,
    save_snapshot: bool = False,
    _: None = Depends(verify_cloudsea_token),
):
    req = _resolve_location(spot_id, viewpoint_id, lat, lng, elevation, None)
    target = date_cls.fromisoformat(date)
    payload = await run_backtest_prediction(
        req=req,
        target_date=target,
        window_start=window_start,
        window_end=window_end,
    )
    label = None
    if spot_id and viewpoint_id:
        label = get_label(spot_id, viewpoint_id, date, window_start, window_end)
        if save_snapshot:
            for row in payload["raw_meteo"]:
                save_meteo_hourly(
                    spot_id=spot_id,
                    viewpoint_id=viewpoint_id,
                    lat=req.lat,
                    lng=req.lng,
                    elevation=req.elevation,
                    ts=row["time"],
                    source="historical_forecast",
                    raw=row,
                )
            save_prediction_run(
                date_key=date,
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
                model_version="fuzzy_v2_archetype",
                hours=payload["prediction"]["hours"],
                label_id=label["id"] if label else None,
            )
    payload["label"] = label
    return payload


@router.get("/api/internal/cloudsea/label-session")
async def label_session(
    spot_id: str,
    viewpoint_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    window_start: int = 3,
    window_end: int = 7,
    _: None = Depends(verify_cloudsea_token),
):
    req = _resolve_location(spot_id, viewpoint_id, None, None, None, None)
    target = date_cls.fromisoformat(date)
    backtest = await run_backtest_prediction(
        req=req,
        target_date=target,
        window_start=window_start,
        window_end=window_end,
    )
    label = get_label(spot_id, viewpoint_id, date, window_start, window_end)
    if spot_id and viewpoint_id:
        for row in backtest["raw_meteo"]:
            save_meteo_hourly(
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
                lat=req.lat,
                lng=req.lng,
                elevation=req.elevation,
                ts=str(row["time"]),
                source="historical_forecast",
                raw=row,
            )
    window_hours = [
        h
        for h in backtest["prediction"]["hours"]
        if window_start <= dt_cls.fromisoformat(h["time"]).hour < window_end
    ]
    return {
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "date": date,
        "label": label,
        "raw_meteo": backtest["raw_meteo"],
        "sunrise_window_summary": backtest["sunrise_window_summary"],
        "hours": window_hours,
    }


@router.get("/api/internal/cloudsea/calendar")
async def cloudsea_calendar(
    spot_id: str,
    viewpoint_id: str,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    _: None = Depends(verify_cloudsea_token),
):
    return {"month": month, "labels": calendar_summary(spot_id, viewpoint_id, month)}


@router.get("/api/internal/cloudsea/accuracy")
async def cloudsea_accuracy(
    spot_id: str,
    viewpoint_id: str,
    _: None = Depends(verify_cloudsea_token),
):
    req = _resolve_location(spot_id, viewpoint_id, None, None, None, None)
    labels = list_labels(spot_id=spot_id, viewpoint_id=viewpoint_id)
    details = []
    correct = 0
    for label in labels:
        backtest = await run_backtest_prediction(
            req=req,
            target_date=date_cls.fromisoformat(label["date"]),
        )
        summary = backtest.get("sunrise_window_summary") or {}
        peak_prob = summary.get("max_cloudsea_prob", 0)
        actual_pos = label["status"] in ("full", "partial")
        pred_pos = peak_prob >= 50
        ok = actual_pos == pred_pos
        if ok:
            correct += 1
        details.append(
            {
                "date": label["date"],
                "status": label["status"],
                "peak_prob": peak_prob,
                "scenario": summary.get("scenario"),
                "predicted_positive": pred_pos,
                "correct": ok,
            }
        )
    total = len(details)
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else None,
        "details": details,
    }


@router.get("/api/internal/cloudsea/labels")
async def cloudsea_labels_list(
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
    month: Optional[str] = None,
    _: None = Depends(verify_cloudsea_token),
):
    return {"labels": list_labels(spot_id=spot_id, viewpoint_id=viewpoint_id, month=month)}


@router.post("/api/internal/cloudsea/labels")
async def cloudsea_labels_upsert(body: LabelBody, _: None = Depends(verify_cloudsea_token)):
    row = upsert_label(
        spot_id=body.spot_id,
        viewpoint_id=body.viewpoint_id,
        date_key=body.date,
        status=body.status,
        window_start=body.window_start,
        window_end=body.window_end,
        confidence=body.confidence,
        notes=body.notes,
    )
    return {"label": row}
