from __future__ import annotations

import hashlib
import threading
import time

from app.services.cache import _get_redis

_memory_lock = threading.Lock()
_memory_buckets: dict[str, tuple[float, int]] = {}


def _safe_key(scope: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"rate:v1:{scope}:{digest}"


def consume_rate_limit(
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int]:
    if limit <= 0:
        return False, max(window_seconds, 1)
    key = _safe_key(scope, identity)
    client = _get_redis()
    if client is not None:
        try:
            value = int(client.incr(key))
            if value == 1:
                client.expire(key, window_seconds)
            ttl = int(client.ttl(key))
            return value <= limit, max(ttl, 1)
        except Exception:
            pass

    now = time.monotonic()
    with _memory_lock:
        started, count = _memory_buckets.get(key, (now, 0))
        if now - started >= window_seconds:
            started, count = now, 0
        count += 1
        _memory_buckets[key] = (started, count)
        retry_after = max(int(window_seconds - (now - started)), 1)
        return count <= limit, retry_after


def reset_memory_rate_limits() -> None:
    with _memory_lock:
        _memory_buckets.clear()
