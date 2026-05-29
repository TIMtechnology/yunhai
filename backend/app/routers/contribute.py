from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.services.cloudsea_store import get_label, upsert_label
from app.services.community_store import (
    COMMUNITY_SPOT_ID,
    assert_contributor_active,
    assert_label_date_allowed,
    calendar_summary_extended,
    check_daily_quota,
    community_label_keys,
    get_community_location,
    get_community_location_by_curated_spot,
    get_contributor_stats,
    label_exists,
    list_community_locations,
    resolve_or_create_location,
    update_community_location,
    validate_contributor_id,
    validate_sunrise_quality,
)
from app.services.contribute_rate_limit import check_contribute_rate_limit
from app.services.label_session import build_label_session_payload

router = APIRouter(prefix="/api/contribute", tags=["contribute"])
TZ = ZoneInfo("Asia/Shanghai")


class ContributeLabelBody(BaseModel):
    spot_id: Optional[str] = None
    viewpoint_id: Optional[str] = None
    location_id: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    elevation: Optional[float] = None
    name: Optional[str] = None
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: str = Field(..., pattern=r"^(none|partial|full)$")
    window_start: int = 3
    window_end: int = 7
    notes: str = ""
    sunrise_quality: Optional[Literal["visible", "blocked", "unshootable"]] = None


class UpdateLocationBody(BaseModel):
    name: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    elevation: Optional[float] = None


class RegisterLocationBody(BaseModel):
    name: str
    lat: float
    lng: float
    elevation: Optional[float] = None
    source: str = "poi"


def _require_contribute_enabled() -> None:
    if not settings.cloudsea_enabled or not settings.cloudsea_contribute_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def get_contributor_id(x_contributor_id: str = Header(default="", alias="X-Contributor-Id")) -> str:
    _require_contribute_enabled()
    try:
        validate_contributor_id(x_contributor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return x_contributor_id


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


async def _rate_limit(request: Request) -> None:
    contributor_id = request.headers.get("x-contributor-id", "").strip()
    bucket_key = contributor_id or _client_ip(request)
    try:
        check_contribute_rate_limit(bucket_key)
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def _resolve_label_target(
    body: ContributeLabelBody,
    contributor_id: str,
) -> tuple[str, str, Optional[str], dict]:
    if body.location_id:
        loc = get_community_location(body.location_id)
        if not loc:
            raise HTTPException(status_code=404, detail="社区点位未找到")
        spot_id, viewpoint_id = community_label_keys(body.location_id)
        return spot_id, viewpoint_id, body.location_id, loc

    if body.lat is not None and body.lng is not None:
        loc = resolve_or_create_location(
            contributor_id=contributor_id,
            lat=body.lat,
            lng=body.lng,
            name=body.name,
            elevation=body.elevation,
            source="poi",
        )
        spot_id, viewpoint_id = community_label_keys(loc["id"])
        return spot_id, viewpoint_id, loc["id"], loc

    if body.spot_id and body.viewpoint_id:
        return body.spot_id, body.viewpoint_id, None, {}

    raise HTTPException(status_code=400, detail="需提供 location_id、lat/lng 或 spot_id+viewpoint_id")


def _review_status_for(contributor_id: str, spot_id: str) -> str:
    if spot_id == COMMUNITY_SPOT_ID and settings.cloudsea_community_auto_approve:
        return "approved"
    if settings.cloudsea_auto_approve_trusted:
        from app.services.community_store import get_contributor

        contrib = get_contributor(contributor_id) or {}
        if contrib.get("trust_level") == "trusted":
            return "approved"
    return "pending"


@router.get("/cloudsea/stats")
async def contribute_stats(
    contributor_id: str = Depends(get_contributor_id),
    _: None = Depends(_rate_limit),
):
    return get_contributor_stats(contributor_id)


@router.get("/locations/mine")
async def my_locations(
    contributor_id: str = Depends(get_contributor_id),
    _: None = Depends(_rate_limit),
):
    return {"locations": list_community_locations(contributor_id)}


@router.get("/locations/by-curated/{spot_id}")
async def location_by_curated(spot_id: str):
    _require_contribute_enabled()
    loc = get_community_location_by_curated_spot(spot_id)
    if not loc:
        raise HTTPException(status_code=404, detail="未找到关联社区点位")
    return {
        "id": loc["id"],
        "name": loc["name"],
        "lat": loc["lat"],
        "lng": loc["lng"],
        "elevation": loc.get("elevation"),
        "approved_label_count": loc.get("approved_label_count", 0),
        "curated_spot_id": loc.get("curated_spot_id"),
    }


@router.get("/locations/{location_id}")
async def public_location(location_id: str):
    _require_contribute_enabled()
    loc = get_community_location(location_id)
    if not loc:
        raise HTTPException(status_code=404, detail="点位未找到")
    return {
        "id": loc["id"],
        "name": loc["name"],
        "lat": loc["lat"],
        "lng": loc["lng"],
        "elevation": loc.get("elevation"),
        "approved_label_count": loc.get("approved_label_count", 0),
        "curated_spot_id": loc.get("curated_spot_id"),
    }


@router.post("/locations")
async def register_location(
    body: RegisterLocationBody,
    contributor_id: str = Depends(get_contributor_id),
    _: None = Depends(_rate_limit),
):
    try:
        assert_contributor_active(contributor_id)
        loc = resolve_or_create_location(
            contributor_id=contributor_id,
            lat=body.lat,
            lng=body.lng,
            name=body.name,
            elevation=body.elevation,
            source=body.source,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"location": loc}


@router.patch("/locations/{location_id}")
async def patch_location(
    location_id: str,
    body: UpdateLocationBody,
    contributor_id: str = Depends(get_contributor_id),
    _: None = Depends(_rate_limit),
):
    if body.name is None and body.lat is None and body.lng is None and body.elevation is None:
        raise HTTPException(status_code=400, detail="请至少提供 name、lat、lng 或 elevation 之一")
    try:
        assert_contributor_active(contributor_id)
        loc = update_community_location(
            location_id=location_id,
            contributor_id=contributor_id,
            name=body.name,
            lat=body.lat,
            lng=body.lng,
            elevation=body.elevation,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"location": loc}


@router.get("/cloudsea/label-session")
async def contribute_label_session(
    request: Request,
    contributor_id: str = Depends(get_contributor_id),
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
    location_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    name: Optional[str] = None,
    elevation: Optional[float] = None,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    window_start: int = 3,
    window_end: int = 7,
):
    await _rate_limit(request)
    try:
        assert_label_date_allowed(date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if location_id:
        spot_id, viewpoint_id = community_label_keys(location_id)
    elif lat is not None and lng is not None:
        loc = resolve_or_create_location(
            contributor_id=contributor_id,
            lat=lat,
            lng=lng,
            name=name,
            elevation=elevation,
            source="poi",
        )
        location_id = loc["id"]
        spot_id, viewpoint_id = community_label_keys(location_id)
        name = loc["name"]
    elif not (spot_id and viewpoint_id):
        raise HTTPException(status_code=400, detail="参数不足")

    payload = await build_label_session_payload(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        date_key=date,
        window_start=window_start,
        window_end=window_end,
        location_id=location_id,
        location_name=name,
    )
    payload["stats"] = get_contributor_stats(contributor_id)
    return payload


@router.get("/cloudsea/calendar")
async def contribute_calendar(
    request: Request,
    contributor_id: str = Depends(get_contributor_id),
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
    location_id: Optional[str] = None,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    await _rate_limit(request)
    if location_id:
        spot_id, viewpoint_id = community_label_keys(location_id)
    if not (spot_id and viewpoint_id):
        raise HTTPException(status_code=400, detail="需提供 location_id 或 spot_id+viewpoint_id")
    return {
        "month": month,
        "labels": calendar_summary_extended(spot_id, viewpoint_id, month),
    }


@router.post("/cloudsea/labels")
async def contribute_save_label(
    body: ContributeLabelBody,
    request: Request,
    contributor_id: str = Depends(get_contributor_id),
):
    await _rate_limit(request)
    try:
        assert_contributor_active(contributor_id)
        assert_label_date_allowed(body.date)
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        validate_sunrise_quality(body.sunrise_quality)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    spot_id, viewpoint_id, location_id, loc = _resolve_label_target(body, contributor_id)
    is_new = not label_exists(
        spot_id, viewpoint_id, body.date, body.window_start, body.window_end
    )
    if is_new:
        try:
            check_daily_quota(contributor_id)
        except PermissionError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    lat = body.lat or (loc.get("lat") if loc else None)
    lng = body.lng or (loc.get("lng") if loc else None)
    elevation = body.elevation or (loc.get("elevation") if loc else None)
    location_name = body.name or (loc.get("name") if loc else None)
    review_status = _review_status_for(contributor_id, spot_id)

    row = upsert_label(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        date_key=body.date,
        status=body.status,
        window_start=body.window_start,
        window_end=body.window_end,
        notes=body.notes,
        labeled_by=contributor_id,
        contributor_id=contributor_id,
        location_id=location_id,
        lat=lat,
        lng=lng,
        location_name=location_name,
        review_status=review_status,
        sunrise_quality=body.sunrise_quality,
    )
    curated = None
    if location_id and review_status == "approved":
        from app.services.curate_service import maybe_auto_curate_location

        curated = maybe_auto_curate_location(location_id)
    if review_status == "pending":
        msg = "已提交，待审核"
    elif curated and not curated.get("already_curated"):
        msg = f"已保存，已加入精选（{curated['spot_id']}）"
    else:
        msg = "已保存"
    return {
        "label": row,
        "message": msg,
        "curated_spot_id": (curated or {}).get("spot_id") or (loc.get("curated_spot_id") if loc else None),
        "stats": get_contributor_stats(contributor_id),
    }
