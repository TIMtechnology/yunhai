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
_redis_available: bool | None = None


def cache_ping() -> bool:
    """检测 Redis 是否可用（结果进程内缓存）。"""
    global _redis_available
    if _redis_available is not None:
        return _redis_available
    client = _get_redis()
    if not client:
        _redis_available = False
        return False
    try:
        client.ping()
        _redis_available = True
    except Exception:
        _redis_available = False
    return _redis_available


def cache_status() -> dict[str, Any]:
    backend = "redis" if cache_ping() else "memory"
    return {
        "backend": backend,
        "redis_url": settings.redis_url if backend == "redis" else None,
        "memory_entries": len(_memory_cache),
    }


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


def cache_delete(key: str) -> None:
    client = _get_redis()
    if client:
        try:
            client.delete(key)
            return
        except Exception:
            pass
    _memory_cache.pop(key, None)


def cache_delete_pattern(pattern: str) -> int:
    client = _get_redis()
    if client:
        try:
            keys = list(client.scan_iter(match=pattern, count=200))
            if keys:
                client.delete(*keys)
            return len(keys)
        except Exception:
            pass
    prefix = pattern.rstrip("*")
    doomed = [k for k in list(_memory_cache) if k.startswith(prefix)]
    for k in doomed:
        _memory_cache.pop(k, None)
    return len(doomed)
