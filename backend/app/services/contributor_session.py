from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from app.config import settings
from app.services.community_store import validate_contributor_id

COOKIE_NAME = "yunhai_contributor_session"
TOKEN_VERSION = "v1"


def _secret() -> bytes:
    raw = settings.contributor_session_secret.strip()
    if len(raw.encode("utf-8")) < 32:
        raise RuntimeError("CONTRIBUTOR_SESSION_SECRET 必须至少 32 字节")
    return raw.encode("utf-8")


def _signature(payload: str) -> str:
    digest = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_contributor_session(contributor_id: str | None = None) -> tuple[str, str]:
    cid = contributor_id or f"cid_{secrets.token_urlsafe(18)}"
    validate_contributor_id(cid)
    expires_at = int(time.time()) + max(int(settings.contributor_session_days), 1) * 86400
    payload = f"{TOKEN_VERSION}.{cid}.{expires_at}"
    return cid, f"{payload}.{_signature(payload)}"


def verify_contributor_session(token: str) -> str:
    try:
        version, contributor_id, expires_raw, supplied_signature = token.split(".", 3)
        expires_at = int(expires_raw)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("无效的贡献者会话") from exc
    if version != TOKEN_VERSION or expires_at < int(time.time()):
        raise ValueError("无效或已过期的贡献者会话")
    validate_contributor_id(contributor_id)
    payload = f"{version}.{contributor_id}.{expires_at}"
    if not hmac.compare_digest(supplied_signature, _signature(payload)):
        raise ValueError("无效的贡献者会话")
    return contributor_id
