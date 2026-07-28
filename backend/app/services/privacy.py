from __future__ import annotations

import hashlib
import hmac

from app.config import settings


def pseudonymize_ip(ip: str) -> str:
    salt = settings.analytics_ip_salt.strip()
    if len(salt.encode("utf-8")) < 32 or not ip or ip == "unknown":
        return "ip_unavailable"
    digest = hmac.new(
        salt.encode("utf-8"),
        ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"ip_{digest[:24]}"
