from __future__ import annotations

import hashlib

from app.services.cache import cache_get, cache_set

FREEZE_TTL_SECONDS = 14 * 86400


def _freeze_key(location_identity: str, target_time: str) -> str:
    identity = hashlib.sha256(location_identity.encode("utf-8")).hexdigest()[:24]
    return f"prediction-freeze:v1:{identity}:{target_time}"


def freeze_probability(
    location_identity: str,
    target_time: str,
    probability: int,
) -> int:
    """Persist the first retrospective probability so later refreshes cannot rewrite history."""
    key = _freeze_key(location_identity, target_time)
    cached = cache_get(key)
    if isinstance(cached, (int, float)):
        return max(0, min(100, int(cached)))

    frozen = max(0, min(100, int(probability)))
    cache_set(key, frozen, ttl=FREEZE_TTL_SECONDS)
    return frozen
