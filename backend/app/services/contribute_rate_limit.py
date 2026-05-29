from __future__ import annotations

import json
import time
from typing import Optional

from app.services.cache import cache_get, cache_set

_CONTRIBUTE_RL_PREFIX = "contrib_rl:"


def check_contribute_rate_limit(bucket_key: str, *, limit: int = 300, window_seconds: int = 3600) -> None:
    if not bucket_key:
        return
    key = f"{_CONTRIBUTE_RL_PREFIX}{bucket_key}"
    bucket = cache_get(key)
    now = time.time()
    if not bucket:
        cache_set(key, {"count": 1, "start": now}, ttl=window_seconds)
        return
    if now - bucket["start"] > window_seconds:
        cache_set(key, {"count": 1, "start": now}, ttl=window_seconds)
        return
    if bucket["count"] >= limit:
        raise PermissionError("请求过于频繁，请稍后再试")
    bucket["count"] += 1
    cache_set(key, bucket, ttl=window_seconds)
