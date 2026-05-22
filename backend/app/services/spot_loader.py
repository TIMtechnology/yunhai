from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.models.schemas import ScenicSpot, SpotSearchResult, Viewpoint

_spots: dict[str, ScenicSpot] = {}


def _spots_dir() -> Path:
    base = Path(__file__).resolve().parents[2]
    configured = Path(settings.scenic_spots_dir)
    if configured.is_absolute():
        return configured
    return (base / configured).resolve()


def load_spots() -> dict[str, ScenicSpot]:
    global _spots
    if _spots:
        return _spots
    directory = _spots_dir()
    for file in directory.glob("*.json"):
        data = json.loads(file.read_text(encoding="utf-8"))
        spot = ScenicSpot(**data)
        _spots[spot.id] = spot
    return _spots


def get_spot(spot_id: str) -> ScenicSpot | None:
    return load_spots().get(spot_id)


def search_spots(query: str) -> list[SpotSearchResult]:
    query = query.strip().lower()
    if not query:
        return []
    results: list[SpotSearchResult] = []
    for spot in load_spots().values():
        names = [spot.name.lower(), *[a.lower() for a in spot.aliases]]
        if any(query in name or name in query for name in names):
            vp = spot.viewpoints[0] if spot.viewpoints else None
            results.append(
                SpotSearchResult(
                    id=spot.id,
                    name=spot.name,
                    region=spot.region,
                    source="curated",
                    lat=vp.lat if vp else None,
                    lng=vp.lng if vp else None,
                    peak_elevation=spot.peak_elevation,
                    viewpoint_count=len(spot.viewpoints),
                )
            )
    return results


def get_viewpoint(spot_id: str, viewpoint_id: str) -> Viewpoint | None:
    spot = get_spot(spot_id)
    if not spot:
        return None
    for vp in spot.viewpoints:
        if vp.id == viewpoint_id:
            return vp
    return None
