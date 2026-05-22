from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import httpx

from app.adapters.nsmc_wms import clamp_to_latest_satellite_time, normalize_bbox, parse_time, satellite_time_available

GIBS_WMS_URL = "https://gibs-a.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
HIMawari_IR_LAYER = "Himawari_AHI_Band13_Clean_Infrared"
MIN_VALID_BYTES = 1500
NSMC_BLACK_PLACEHOLDER_BYTES = 4723


def to_gibs_time(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_valid_cloud_image(content: bytes) -> bool:
    if len(content) < MIN_VALID_BYTES:
        return False
    if content.startswith(b"<?xml") or content.startswith(b"{"):
        return False
    if not content.startswith(b"\xff\xd8\xff"):
        return False
    # NSMC 固定返回 4723 字节纯黑占位 JPEG
    if len(content) == NSMC_BLACK_PLACEHOLDER_BYTES:
        return False

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content))
        sample = [
            img.getpixel((x, y))
            for x in range(0, img.size[0], max(1, img.size[0] // 12))
            for y in range(0, img.size[1], max(1, img.size[1] // 12))
        ]
        values = [p[0] if isinstance(p, tuple) else p for p in sample]
        return max(values) > 5 and len(set(values)) > 2
    except Exception:
        return len(content) > NSMC_BLACK_PLACEHOLDER_BYTES


async def fetch_himawari_ir_for_bbox(
    bbox: dict[str, float],
    time: datetime,
    width: int = 512,
    height: int = 512,
) -> dict:
    bbox = normalize_bbox(bbox)
    time_str = to_gibs_time(time)
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": "1.1.1",
        "LAYERS": HIMawari_IR_LAYER,
        "STYLES": "",
        "SRS": "EPSG:4326",
        "BBOX": f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}",
        "WIDTH": width,
        "HEIGHT": height,
        "FORMAT": "image/jpeg",
        "TIME": time_str,
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.get(GIBS_WMS_URL, params=params)
        resp.raise_for_status()
        content = resp.content

    lng_span = (bbox["east"] - bbox["west"]) / 2
    lat_span = (bbox["north"] - bbox["south"]) / 2
    utc = time.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return {
        "content": content,
        "bounds": bbox,
        "datetime_utc": utc.strftime("%Y%m%d%H00"),
        "layer": HIMawari_IR_LAYER,
        "span_lng": lng_span,
        "span_lat": lat_span,
        "valid": is_valid_cloud_image(content),
        "source": "gibs_himawari_b13",
    }


async def fetch_himawari_best_effort(
    bbox: dict[str, float],
    time: datetime,
    lookback_hours: int = 24,
) -> dict | None:
    target = clamp_to_latest_satellite_time(time)
    for offset in range(lookback_hours + 1):
        candidate = target - timedelta(hours=offset)
        if not satellite_time_available(candidate):
            continue
        result = await fetch_himawari_ir_for_bbox(bbox, candidate)
        if result["valid"]:
            result["lookback_hours"] = offset
            return result
    return None


__all__ = ["fetch_himawari_best_effort", "fetch_himawari_ir_for_bbox", "is_valid_cloud_image", "parse_time"]
