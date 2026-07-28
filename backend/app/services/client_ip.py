from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from starlette.requests import Request


def _parse_ip(raw: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not raw:
        return None
    value = raw.strip()
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def resolve_client_ip(
    *,
    direct_ip: str | None,
    forwarded_for: str | None,
    trusted_proxy_cidrs: Sequence[str],
) -> str:
    direct = _parse_ip(direct_ip)
    if direct is None:
        return "unknown"
    trusted_networks = tuple(ipaddress.ip_network(cidr) for cidr in trusted_proxy_cidrs)
    if not any(direct in network for network in trusted_networks):
        return str(direct)
    for candidate in (forwarded_for or "").split(","):
        parsed = _parse_ip(candidate)
        if parsed is not None:
            return str(parsed)
    return str(direct)


def client_ip_from_request(request: Request, trusted_proxy_cidrs: Sequence[str]) -> str:
    return resolve_client_ip(
        direct_ip=request.client.host if request.client else None,
        forwarded_for=request.headers.get("x-forwarded-for"),
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )
