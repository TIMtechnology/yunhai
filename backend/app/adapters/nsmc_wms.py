from __future__ import annotations

import struct
import zlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

NSMC_WMS_URL = "https://data.nsmc.org.cn/NSMCAPI/v1/nsmc/image/wms/compose"
DEFAULT_SPAN_LNG = 1.8
DEFAULT_SPAN_LAT = 1.2
MIN_BBOX_SPAN = 0.08
MAX_BBOX_SPAN = 8.0
BLANK_SIZE_THRESHOLD = 1800
JPEG_MIN_SIZE = 4000

SATellite_MAX_FUTURE = timedelta(hours=1)
SATellite_MAX_AGE = timedelta(hours=72)


def compute_bbox(
    lat: float,
    lng: float,
    span_lng: float = DEFAULT_SPAN_LNG,
    span_lat: float = DEFAULT_SPAN_LAT,
) -> dict[str, float]:
    return normalize_bbox(
        {
            "west": lng - span_lng,
            "south": lat - span_lat,
            "east": lng + span_lng,
            "north": lat + span_lat,
        }
    )


def normalize_bbox(bbox: dict[str, float]) -> dict[str, float]:
    west = min(bbox["west"], bbox["east"])
    east = max(bbox["west"], bbox["east"])
    south = min(bbox["south"], bbox["north"])
    north = max(bbox["south"], bbox["north"])

    lng_span = east - west
    lat_span = north - south
    if lng_span < MIN_BBOX_SPAN:
        pad = (MIN_BBOX_SPAN - lng_span) / 2
        west -= pad
        east += pad
    if lat_span < MIN_BBOX_SPAN:
        pad = (MIN_BBOX_SPAN - lat_span) / 2
        south -= pad
        north += pad

    lng_span = east - west
    lat_span = north - south
    if lng_span > MAX_BBOX_SPAN:
        center = (west + east) / 2
        west = center - MAX_BBOX_SPAN / 2
        east = center + MAX_BBOX_SPAN / 2
    if lat_span > MAX_BBOX_SPAN:
        center = (south + north) / 2
        south = center - MAX_BBOX_SPAN / 2
        north = center + MAX_BBOX_SPAN / 2

    return {"west": west, "south": south, "east": east, "north": north}


def parse_time(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return dt


def to_utc_hourly(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y%m%d%H00")


def satellite_time_available(dt: datetime) -> bool:
    now = datetime.now(timezone.utc)
    utc = dt.astimezone(timezone.utc)
    if utc > now + SATellite_MAX_FUTURE:
        return False
    if utc < now - SATellite_MAX_AGE:
        return False
    return True


def clamp_to_latest_satellite_time(dt: datetime) -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    utc = dt.astimezone(timezone.utc)
    if utc > now:
        return now
    return utc.replace(minute=0, second=0, microsecond=0)


def resolve_bbox_span(
    span_lng: Optional[float],
    span_lat: Optional[float],
    spot_cloud_region: Optional[dict] = None,
) -> tuple[float, float]:
    region = spot_cloud_region or {}
    lng = span_lng if span_lng is not None else region.get("span_lng", DEFAULT_SPAN_LNG)
    lat = span_lat if span_lat is not None else region.get("span_lat", DEFAULT_SPAN_LAT)
    return float(lng), float(lat)


def is_blank_image(content: bytes) -> bool:
    """NSMC 无数据时 PNG 返回约 1KB 透明占位图；JPEG 有效图通常 >4KB。"""
    if content.startswith(b"\xff\xd8\xff"):
        if len(content) <= 5000:
            return True
        return len(content) < JPEG_MIN_SIZE

    if len(content) < BLANK_SIZE_THRESHOLD:
        return True

    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False

    # 采样 IDAT 解压后的像素，判断是否几乎全透明/同色
    try:
        pos = 8
        raw = bytearray()
        while pos + 8 <= len(content):
            length = struct.unpack(">I", content[pos : pos + 4])[0]
            chunk_type = content[pos + 4 : pos + 8]
            data = content[pos + 8 : pos + 8 + length]
            if chunk_type == b"IDAT":
                raw.extend(zlib.decompress(data))
            pos += 12 + length
            if chunk_type == b"IEND":
                break

        if len(raw) < 16:
            return True

        # RGBA 每 4 字节，统计非透明像素
        non_transparent = 0
        step = max(4, (len(raw) // 4) // 4000 * 4)
        for i in range(0, len(raw) - 3, step):
            if raw[i + 3] > 12:
                non_transparent += 1
        return non_transparent < 8
    except Exception:
        return len(content) < BLANK_SIZE_THRESHOLD


async def fetch_geos_irx_for_bbox(
    bbox: dict[str, float],
    time: datetime,
    width: int = 512,
    height: int = 512,
) -> dict:
    bbox = normalize_bbox(bbox)
    datetime_str = to_utc_hourly(time)
    params = {
        "layers": "GEOS_IRX",
        "datetime": datetime_str,
        "request": "GetMap",
        "bbox": f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}",
        "width": width,
        "height": height,
        "version": "1.1.0",
        "format": "jpeg",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(NSMC_WMS_URL, params=params)
        resp.raise_for_status()
        content = resp.content

    lng_span = (bbox["east"] - bbox["west"]) / 2
    lat_span = (bbox["north"] - bbox["south"]) / 2
    return {
        "content": content,
        "bounds": bbox,
        "datetime_utc": datetime_str,
        "layer": "GEOS_IRX",
        "span_lng": lng_span,
        "span_lat": lat_span,
        "valid": not is_blank_image(content),
    }


async def fetch_geos_irx_image(
    lat: float,
    lng: float,
    time: datetime,
    span_lng: float = DEFAULT_SPAN_LNG,
    span_lat: float = DEFAULT_SPAN_LAT,
    width: int = 512,
    height: int = 512,
) -> dict:
    bbox = compute_bbox(lat, lng, span_lng, span_lat)
    return await fetch_geos_irx_for_bbox(bbox, time, width, height)


async def fetch_geos_irx_best_effort(
    bbox: dict[str, float],
    time: datetime,
    lookback_hours: int = 8,
) -> Optional[dict]:
    """向前逐小时回溯，取最近一张有效卫星图。"""
    target = clamp_to_latest_satellite_time(time)
    for offset in range(lookback_hours + 1):
        candidate = target - timedelta(hours=offset)
        if not satellite_time_available(candidate):
            continue
        result = await fetch_geos_irx_for_bbox(bbox, candidate)
        if result["valid"]:
            result["lookback_hours"] = offset
            return result
    return None
