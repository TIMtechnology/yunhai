"""对外 HTTP 请求：禁用 http2 + 连接失败时短暂重试（缓解 Docker DNS 抖动）。"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

_RETRYABLE = (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException)


async def get_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    timeout: float = 45.0,
    retries: int = 2,
) -> dict:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            async with httpx.AsyncClient(timeout=timeout, http2=False) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt + 1 < retries:
                await asyncio.sleep(0.35 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def post_json(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    timeout: float = 45.0,
    retries: int = 3,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            async with httpx.AsyncClient(timeout=timeout, http2=False) as client:
                resp = await client.post(url, headers=headers, json=json)
                return resp
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt + 1 < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
    assert last_exc is not None
    raise last_exc
