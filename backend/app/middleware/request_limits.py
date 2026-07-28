from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.services.client_ip import resolve_client_ip
from app.services.rate_limit import consume_rate_limit


class RequestBodyLimitMiddleware:
    """Buffer bounded HTTP request bodies before passing them downstream.

    Starlette documents wrapping the ASGI ``receive`` callable as the supported
    way to inspect request body events:
    https://www.starlette.io/middleware/#inspecting-or-modifying-the-request
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        maximum = max(int(settings.max_request_body_bytes), 1024)
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > maximum:
            await JSONResponse({"detail": "请求体过大"}, status_code=413)(scope, receive, send)
            return

        messages: list[Message] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                continue
            total += len(message.get("body", b""))
            if total > maximum:
                await JSONResponse({"detail": "请求体过大"}, status_code=413)(scope, receive, send)
                return
            messages.append(message)
            more_body = bool(message.get("more_body", False))

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or not str(scope.get("path", "")).startswith("/api/")
            or str(scope.get("path", "")).startswith("/api/internal/")
        ):
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if path.startswith("/api/advisory/"):
            bucket = "advisory"
            limit = settings.expensive_rate_limit_per_minute
        elif path.startswith(("/api/predict", "/api/weather/", "/api/terrain/", "/api/satellite/")):
            bucket = "expensive"
            limit = settings.expensive_rate_limit_per_minute
        elif path == "/api/contribute/session":
            bucket = "contributor_session"
            limit = settings.contributor_session_rate_limit_per_hour
        else:
            bucket = "api"
            limit = settings.api_rate_limit_per_minute

        headers = Headers(scope=scope)
        client = scope.get("client")
        direct_ip = str(client[0]) if client else None
        identity = resolve_client_ip(
            direct_ip=direct_ip,
            forwarded_for=headers.get("x-forwarded-for"),
            trusted_proxy_cidrs=settings.trusted_proxy_cidrs,
        )
        window = 3600 if bucket == "contributor_session" else 60
        allowed, retry_after = consume_rate_limit(
            scope=bucket,
            identity=identity,
            limit=int(limit),
            window_seconds=window,
        )
        if not allowed:
            await JSONResponse(
                {"detail": "请求过于频繁，请稍后重试"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
