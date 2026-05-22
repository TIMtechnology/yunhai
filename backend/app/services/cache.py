from __future__ import annotations

import json
import time
from typing import Any

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

from app.config import settings

_memory_cache: dict[str, tuple[float, Any]] = {}
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if redis is None:
        return None
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis_client = client
        return client
    except Exception:
        return None


def cache_get(key: str) -> Any | None:
    client = _get_redis()
    if client:
        try:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            pass
    entry = _memory_cache.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if time.time() > expires_at:
        _memory_cache.pop(key, None)
        return None
    return value


def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    ttl = ttl or settings.cache_ttl_seconds
    client = _get_redis()
    if client:
        try:
            client.setex(key, ttl, json.dumps(value, ensure_ascii=False))
            return
        except Exception:
            pass
    _memory_cache[key] = (time.time() + ttl, value)
