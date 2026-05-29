from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.services.analytics_parser import classify_channel, extract_client_ip, parse_user_agent
from app.services.analytics_store import (
    export_csv,
    insert_event,
    query_api_stats,
    query_channels,
    query_clients,
    query_events,
    query_searches,
    query_spots,
    query_summary,
)

router = APIRouter(tags=["analytics"])

_collect_hits: dict[str, list[float]] = defaultdict(list)
_COLLECT_LIMIT = 120
_COLLECT_WINDOW = 60.0

DASHBOARD_ORIGINS = {
    "http://127.0.0.1:8765",
    "http://localhost:8765",
}


class CollectBody(BaseModel):
    event: str = Field(..., max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    page_url: Optional[str] = None
    referrer: Optional[str] = None


def _require_analytics_enabled() -> None:
    if not settings.analytics_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def verify_admin_token(x_analytics_token: str = Header(default="")) -> None:
    _require_analytics_enabled()
    if not settings.analytics_admin_token or x_analytics_token != settings.analytics_admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _cors_headers(origin: str | None) -> dict[str, str]:
    if origin and origin in DASHBOARD_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "X-Analytics-Token, Content-Type",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
        }
    return {}


def _check_collect_rate(ip: str) -> None:
    now = time.time()
    bucket = _collect_hits[ip]
    _collect_hits[ip] = [t for t in bucket if now - t < _COLLECT_WINDOW]
    if len(_collect_hits[ip]) >= _COLLECT_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    _collect_hits[ip].append(now)


def _record_client_event(request: Request, event: str, payload: dict, page_url: str | None, referrer: str | None) -> None:
    referer = referrer or request.headers.get("referer", "")
    ua = request.headers.get("user-agent", "")
    browser, os_name, device = parse_user_agent(ua)
    ip = extract_client_ip(
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        request.client.host if request.client else "",
    )
    channel = classify_channel(referer, page_url)
    insert_event(
        event_type=event[:64],
        ip=ip,
        referer=referer,
        channel=channel,
        browser=browser,
        os=os_name,
        device=device,
        path=page_url or "",
        payload={**payload, "page_url": page_url, "referrer": referer},
    )


@router.post("/api/analytics/collect")
async def collect_event(body: CollectBody, request: Request):
    _require_analytics_enabled()
    ip = extract_client_ip(
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        request.client.host if request.client else "",
    )
    _check_collect_rate(ip)
    allowed = {
        "page_visit",
        "search",
        "poi_search",
        "spot_select",
        "viewpoint_select",
        "predict_custom",
    }
    if body.event not in allowed:
        raise HTTPException(status_code=400, detail="Invalid event")
    _record_client_event(request, body.event, body.payload, body.page_url, body.referrer)
    return {"ok": True}


@router.options("/api/internal/analytics/{path:path}")
async def internal_options(path: str, request: Request):
    headers = _cors_headers(request.headers.get("origin"))
    if not headers:
        raise HTTPException(status_code=403)
    return Response(status_code=204, headers=headers)


def _with_cors(data: Any, request: Request) -> Response:
    import json

    headers = _cors_headers(request.headers.get("origin"))
    headers["Content-Type"] = "application/json; charset=utf-8"
    return Response(content=json.dumps(data, ensure_ascii=False), headers=headers)


@router.get("/api/internal/analytics/summary", dependencies=[Depends(verify_admin_token)])
async def analytics_summary(
    request: Request,
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
):
    return _with_cors(query_summary(from_ts, to_ts), request)


@router.get("/api/internal/analytics/channels", dependencies=[Depends(verify_admin_token)])
async def analytics_channels(request: Request, from_ts: Optional[str] = Query(None, alias="from"), to_ts: Optional[str] = Query(None, alias="to")):
    return _with_cors(query_channels(from_ts, to_ts), request)


@router.get("/api/internal/analytics/spots", dependencies=[Depends(verify_admin_token)])
async def analytics_spots(request: Request, from_ts: Optional[str] = Query(None, alias="from"), to_ts: Optional[str] = Query(None, alias="to")):
    return _with_cors(query_spots(from_ts, to_ts), request)


@router.get("/api/internal/analytics/searches", dependencies=[Depends(verify_admin_token)])
async def analytics_searches(request: Request, from_ts: Optional[str] = Query(None, alias="from"), to_ts: Optional[str] = Query(None, alias="to")):
    return _with_cors(query_searches(from_ts, to_ts), request)


@router.get("/api/internal/analytics/clients", dependencies=[Depends(verify_admin_token)])
async def analytics_clients(request: Request, from_ts: Optional[str] = Query(None, alias="from"), to_ts: Optional[str] = Query(None, alias="to")):
    return _with_cors(query_clients(from_ts, to_ts), request)


@router.get("/api/internal/analytics/api-stats", dependencies=[Depends(verify_admin_token)])
async def analytics_api_stats(request: Request, from_ts: Optional[str] = Query(None, alias="from"), to_ts: Optional[str] = Query(None, alias="to")):
    return _with_cors(query_api_stats(from_ts, to_ts), request)


@router.get("/api/internal/analytics/events", dependencies=[Depends(verify_admin_token)])
async def analytics_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
):
    return _with_cors(query_events(limit=limit, from_ts=from_ts, to_ts=to_ts), request)


@router.get("/api/internal/analytics/export.csv", dependencies=[Depends(verify_admin_token)])
async def analytics_export(
    request: Request,
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    limit: int = Query(5000, ge=1, le=20000),
):
    csv_text = export_csv(from_ts=from_ts, to_ts=to_ts, limit=limit)
    headers = _cors_headers(request.headers.get("origin"))
    headers["Content-Type"] = "text/csv; charset=utf-8"
    headers["Content-Disposition"] = "attachment; filename=yunhai-analytics.csv"
    return Response(content=csv_text, headers=headers)
