from __future__ import annotations

import asyncio

from datetime import date as date_cls
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
from app.services.community_store import (
    COMMUNITY_SPOT_ID,
    get_community_location,
    list_review_queue,
    review_label,
    validate_sunrise_quality,
)
from app.services.curate_service import curate_community_location, run_model_training
from app.services.label_session import build_label_session_payload
from app.services.predictor import backtest_sunrise_peak, run_backtest_prediction
from app.services.spot_loader import get_spot, get_viewpoint
from app.services.cache import cache_get, cache_set
from app.engine.ml_eligibility import spot_model_path

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
    sunrise_quality: Optional[str] = Field(default=None, pattern=r"^(visible|blocked|unshootable)$")


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
            "Access-Control-Allow-Headers": "X-Cloudsea-Token, X-Contributor-Id, Content-Type",
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
        if spot_id == COMMUNITY_SPOT_ID:
            loc = get_community_location(viewpoint_id)
            if not loc:
                raise HTTPException(status_code=404, detail="社区点位未找到")
            return PredictRequest(
                lat=loc["lat"],
                lng=loc["lng"],
                elevation=loc.get("elevation"),
                name=loc["name"],
                spot_id=None,
                hours=24,
            )
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
            viewpoint_id=viewpoint_id,
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
    try:
        return await build_label_session_payload(
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
            date_key=date,
            window_start=window_start,
            window_end=window_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    refresh: bool = False,
    _: None = Depends(verify_cloudsea_token),
):
    model_path = spot_model_path(spot_id, viewpoint_id)
    model_tag = int(model_path.stat().st_mtime) if model_path.is_file() else 0
    cache_key = f"cloudsea_accuracy:{spot_id}:{viewpoint_id}:{model_tag}"
    if not refresh:
        cached = cache_get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    req = _resolve_location(spot_id, viewpoint_id, None, None, None, None)
    labels = list_labels(spot_id=spot_id, viewpoint_id=viewpoint_id)
    sem = asyncio.Semaphore(2)

    async def _eval_label(label: dict) -> dict:
        async with sem:
            peak = await backtest_sunrise_peak(
                req=req,
                target_date=date_cls.fromisoformat(label["date"]),
                model_tag=model_tag,
                prefer_cached_meteo=True,
            )
        peak_prob = float(peak.get("peak_prob") or 0)
        actual_pos = label["status"] in ("full", "partial")
        pred_pos = peak_prob >= 50
        return {
            "date": label["date"],
            "status": label["status"],
            "peak_prob": peak_prob,
            "scenario": peak.get("scenario"),
            "predicted_positive": pred_pos,
            "correct": actual_pos == pred_pos,
        }

    details = list(await asyncio.gather(*[_eval_label(label) for label in labels]))
    correct = sum(1 for row in details if row["correct"])
    total = len(details)
    payload = {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else None,
        "details": details,
        "cached": False,
    }
    cache_set(cache_key, payload, ttl=3600)
    return payload


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
    try:
        validate_sunrise_quality(body.sunrise_quality)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = upsert_label(
        spot_id=body.spot_id,
        viewpoint_id=body.viewpoint_id,
        date_key=body.date,
        status=body.status,
        window_start=body.window_start,
        window_end=body.window_end,
        confidence=body.confidence,
        notes=body.notes,
        labeled_by="admin",
        review_status="approved",
        sunrise_quality=body.sunrise_quality,
    )
    return {"label": row}


class ReviewBody(BaseModel):
    review_status: str = Field(..., pattern=r"^(approved|rejected)$")


@router.get("/api/internal/cloudsea/review-queue")
async def cloudsea_review_queue(
    limit: int = 100,
    _: None = Depends(verify_cloudsea_token),
):
    return {"items": list_review_queue(limit=limit)}


@router.post("/api/internal/cloudsea/labels/{label_id}/review")
async def cloudsea_review_label(
    label_id: int,
    body: ReviewBody,
    _: None = Depends(verify_cloudsea_token),
):
    row = review_label(label_id, review_status=body.review_status, reviewed_by="admin")
    if not row:
        raise HTTPException(status_code=404, detail="标注未找到")
    return {"label": row}


@router.post("/api/internal/cloudsea/locations/{location_id}/curate")
async def cloudsea_curate_location(
    location_id: str,
    _: None = Depends(verify_cloudsea_token),
):
    try:
        result = curate_community_location(location_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/api/internal/cloudsea/train")
async def cloudsea_train_model(_: None = Depends(verify_cloudsea_token)):
    try:
        metrics = run_model_training()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return metrics


@router.get("/api/internal/cloudsea/prediction-history")
async def cloudsea_prediction_history(
    spot_id: str,
    viewpoint_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    _: None = Depends(verify_cloudsea_token),
):
    from app.services.prediction_feedback import get_prediction_history

    return get_prediction_history(spot_id, viewpoint_id, date)


@router.get("/api/internal/cloudsea/prediction-history/{access_log_id}")
async def cloudsea_prediction_history_detail(
    access_log_id: int,
    _: None = Depends(verify_cloudsea_token),
):
    from app.services.prediction_feedback import get_prediction_snapshot_detail

    row = get_prediction_snapshot_detail(access_log_id)
    if not row:
        raise HTTPException(status_code=404, detail="访问记录未找到")
    return row


@router.post("/api/internal/cloudsea/reconcile")
async def cloudsea_reconcile_outcomes(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    force: bool = False,
    _: None = Depends(verify_cloudsea_token),
):
    from app.services.prediction_feedback import reconcile_target_date

    return reconcile_target_date(date, force=force)


@router.get("/api/internal/cloudsea/export/feedback")
async def cloudsea_export_feedback(
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    format: str = Query(default="json", pattern=r"^(json|csv)$"),
    _: None = Depends(verify_cloudsea_token),
):
    from fastapi.responses import PlainTextResponse

    from app.services.cloudsea_store import export_prediction_feedback

    payload = export_prediction_feedback(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        month=month,
        export_format=format,
    )
    if format == "csv":
        return PlainTextResponse(content=payload.get("body") or "", media_type="text/csv; charset=utf-8")
    return payload
