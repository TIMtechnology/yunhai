from __future__ import annotations

import json

from typing import Optional

import httpx

from app.config import settings
from app.models.schemas import SpotSearchResult
from app.services.cache import cache_get, cache_set

SEARCH_URL = "http://api.tianditu.gov.cn/v2/search"


def _map_bound(center_lat: Optional[float], center_lng: Optional[float]) -> str:
    """以中心点 ±2° 构造搜索范围，无中心时用全国范围。"""
    if center_lat is None or center_lng is None:
        return "73.66,3.86,135.05,53.55"
    west = max(73.66, center_lng - 2.0)
    east = min(135.05, center_lng + 2.0)
    south = max(3.86, center_lat - 2.0)
    north = min(53.55, center_lat + 2.0)
    return f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"


async def search_poi(
    query: str,
    count: int = 12,
    center_lat: Optional[float] = None,
    center_lng: Optional[float] = None,
) -> list[SpotSearchResult]:
    if not query.strip():
        return []

    bound = _map_bound(center_lat, center_lng)
    cache_key = f"tdt_poi:{query.strip()}:{bound}:{count}"
    cached = cache_get(cache_key)
    if cached:
        return [SpotSearchResult(**item) for item in cached]

    post_str = json.dumps(
        {
            "keyWord": query.strip(),
            "level": "12",
            "mapBound": bound,
            "queryType": "1",
            "start": "0",
            "count": str(count),
        },
        ensure_ascii=False,
    )
    params = {
        "postStr": post_str,
        "type": "query",
        "tk": settings.tianditu_key,
    }

    results: list[SpotSearchResult] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return results

    if data.get("status") not in (None, "0", 0):
        return results

    pois = data.get("pois") or []
    for idx, poi in enumerate(pois[:count]):
        lonlat = poi.get("lonlat", "")
        if not lonlat or "," not in lonlat:
            continue
        lng_str, lat_str = lonlat.split(",", 1)
        try:
            lng = float(lng_str)
            lat = float(lat_str)
        except ValueError:
            continue

        province = poi.get("province") or ""
        city = poi.get("city") or ""
        county = poi.get("county") or ""
        address = poi.get("address") or ""
        region = f"{province}{city}{county}".strip() or address

        results.append(
            SpotSearchResult(
                id=f"poi-{poi.get('hotPointID', idx)}",
                name=poi.get("name", query),
                region=region,
                source="tianditu",
                lat=lat,
                lng=lng,
                peak_elevation=None,
                viewpoint_count=0,
                address=address or None,
            )
        )

    cache_set(cache_key, [r.model_dump() for r in results], ttl=3600)
    return results
