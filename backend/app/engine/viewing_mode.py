"""观云模式解析：精选 JSON / 社区点 / DEM 自动推断。"""

from __future__ import annotations

from typing import Any, Optional

from app.adapters.dem import infer_viewing_mode
from app.services.community_store import get_community_location
from app.services.spot_loader import get_spot, get_viewpoint

DEFAULT_MODE = "valley_fill"


def resolve_viewing_mode(
    *,
    spot_id: str | None,
    viewpoint_id: str | None,
    elevation: float,
    terrain: dict[str, Any] | None = None,
    location_id: str | None = None,
) -> tuple[str, str, str]:
    """返回 (mode, note, source)。"""
    if spot_id and viewpoint_id and spot_id != "community":
        vp = get_viewpoint(spot_id, viewpoint_id)
        spot = get_spot(spot_id)
        if vp and getattr(vp, "viewing_mode", None):
            return str(vp.viewing_mode), "精选观景点配置", "curated"
        if spot and spot.rules.get("viewing_mode"):
            return str(spot.rules["viewing_mode"]), "精选景区规则", "curated"

    if location_id or (spot_id == "community" and viewpoint_id):
        loc_id = location_id or viewpoint_id
        loc = get_community_location(loc_id) if loc_id else None
        if loc and loc.get("viewing_mode"):
            return str(loc["viewing_mode"]), "社区点位配置", "community"

    if terrain:
        mode = str(terrain.get("viewing_mode") or DEFAULT_MODE)
        note = str(terrain.get("viewing_mode_note") or "DEM 自动推断")
        source = str(terrain.get("viewing_mode_source") or "auto_dem")
        return mode, note, source

    mode, note = infer_viewing_mode(
        elev_view=elevation,
        elev_max_5km=elevation,
        relief_5km=0.0,
    )
    return mode, note, "fallback"
