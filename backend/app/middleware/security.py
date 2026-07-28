from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        amap_eval = (
            " 'unsafe-eval'"
            if request.url.path in {"/", "/index.html"}
            else ""
        )
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(self)",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            f"script-src 'self' 'unsafe-inline'{amap_eval} https://webapi.amap.com https://*.amap.com; "
            "worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https: http://webapi.amap.com; "
            "font-src 'self' data:; "
            "connect-src 'self' https://*.amap.com https://restapi.amap.com",
        )
        if response.status_code == 200:
            if request.url.path.startswith("/assets/"):
                response.headers.setdefault(
                    "Cache-Control",
                    "public, max-age=31536000, immutable",
                )
            elif request.url.path in {"/", "/index.html", "/label.html"}:
                response.headers.setdefault("Cache-Control", "no-cache")
        return response
