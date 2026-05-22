from __future__ import annotations

import json
import re
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.services.analytics_parser import classify_channel, extract_client_ip, parse_user_agent
from app.services.analytics_store import insert_event

_SKIP_PREFIXES = (
    "/health",
    "/api/analytics/collect",
    "/api/internal/",
    "/docs",
    "/redoc",
    "/openapi.json",
)

_PREDICT_VP = re.compile(r"^/api/predict/([^/]+)/viewpoint/([^/]+)$")


def _should_skip(path: str) -> bool:
    if not path.startswith("/api"):
        return True
    return any(path.startswith(p) for p in _SKIP_PREFIXES)


def _business_payload(path: str, method: str, query: dict[str, str]) -> tuple[str, dict[str, Any]]:
    if path == "/api/spots/search" and method == "GET":
        return "api_search", {
            "keyword": query.get("q", ""),
            "curated_only": query.get("curated_only", "true"),
        }
    match = _PREDICT_VP.match(path)
    if match and method == "GET":
        return "api_predict_viewpoint", {
            "spot_id": match.group(1),
            "viewpoint_id": match.group(2),
        }
    if path == "/api/predict" and method == "POST":
        return "api_predict", {"source": "post"}
    if path == "/api/satellite/cloud" and method == "GET":
        return "api_satellite", {
            "spot_id": query.get("spot_id"),
            "lat": query.get("lat"),
            "lng": query.get("lng"),
        }
    if path == "/api/weather/raw" and method == "GET":
        return "api_weather_raw", {
            "lat": query.get("lat"),
            "lng": query.get("lng"),
        }
    slug = path.strip("/").replace("/", "_") or "root"
    return f"api_{slug}", {"path": path}


class AnalyticsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.analytics_enabled or _should_skip(request.url.path):
            return await call_next(request)

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - started) * 1000)

        referer = request.headers.get("referer", "")
        ua = request.headers.get("user-agent", "")
        browser, os_name, device = parse_user_agent(ua)
        ip = extract_client_ip(
            request.headers.get("x-forwarded-for"),
            request.headers.get("x-real-ip"),
            request.client.host if request.client else "",
        )
        channel = classify_channel(referer)
        event_type, payload = _business_payload(
            request.url.path,
            request.method,
            {k: v for k, v in request.query_params.items()},
        )
        payload["method"] = request.method

        try:
            insert_event(
                event_type=event_type,
                ip=ip,
                referer=referer,
                channel=channel,
                browser=browser,
                os=os_name,
                device=device,
                path=request.url.path,
                method=request.method,
                status_code=response.status_code,
                duration_ms=duration_ms,
                payload=payload,
            )
        except Exception:
            pass

        return response
