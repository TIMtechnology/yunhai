from __future__ import annotations

import json
from pathlib import Path

from app.config import curated_spots_dir, settings
from app.models.schemas import ScenicSpot, SpotSearchResult, Viewpoint

_spots: dict[str, ScenicSpot] = {}


def _spots_dir() -> Path:
    base = Path(__file__).resolve().parents[2]
    configured = Path(settings.scenic_spots_dir)
    if configured.is_absolute():
        return configured
    return (base / configured).resolve()


def _curated_spots_dir() -> Path | None:
    directory = curated_spots_dir()
    return directory if directory.is_dir() else None


def _spot_json_files() -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for directory in (_spots_dir(), _curated_spots_dir()):
        if not directory:
            continue
        for file in directory.glob("*.json"):
            if file.name.startswith("_"):
                continue
            if file.name in seen:
                continue
            seen.add(file.name)
            files.append(file)
    return files


def load_spots(*, force: bool = False) -> dict[str, ScenicSpot]:
    global _spots
    if _spots and not force:
        return _spots
    if force:
        _spots = {}
    for file in _spot_json_files():
        data = json.loads(file.read_text(encoding="utf-8"))
        spot = ScenicSpot(**data)
        _spots[spot.id] = spot
    return _spots


def reload_spots() -> dict[str, ScenicSpot]:
    return load_spots(force=True)


def get_spot(spot_id: str) -> ScenicSpot | None:
    return load_spots().get(spot_id)


def _superseded_community_spot_ids() -> set[str]:
    return {
        spot.community_location_id
        for spot in load_spots().values()
        if spot.community_location_id
    }


def search_spots(query: str) -> list[SpotSearchResult]:
    query = query.strip().lower()
    by_key: dict[str, SpotSearchResult] = {}
    superseded = _superseded_community_spot_ids()
    for spot in load_spots().values():
        if spot.id in superseded:
            continue
        names = [spot.name.lower(), *[a.lower() for a in spot.aliases]]
        if query and not any(query in name or name in query for name in names):
            continue
        vp = spot.viewpoints[0] if spot.viewpoints else None
        if not vp:
            continue
        dedupe_key = f"{spot.name.lower()}:{round(vp.lat, 4)}:{round(vp.lng, 4)}"
        label = spot.name if spot.id.startswith("cs_") else f"{spot.name} · {spot.id}"
        item = SpotSearchResult(
            id=spot.id,
            name=label,
            region=spot.region,
            source="curated",
            lat=vp.lat,
            lng=vp.lng,
            peak_elevation=spot.peak_elevation,
            viewpoint_count=len(spot.viewpoints),
        )
        prev = by_key.get(dedupe_key)
        if prev is None:
            by_key[dedupe_key] = item
        elif prev.id.startswith("cs_") and not spot.id.startswith("cs_"):
            by_key[dedupe_key] = item
    results = list(by_key.values())
    results.sort(key=lambda item: item.name)
    return results


def get_viewpoint(spot_id: str, viewpoint_id: str) -> Viewpoint | None:
    spot = get_spot(spot_id)
    if not spot:
        return None
    for vp in spot.viewpoints:
        if vp.id == viewpoint_id:
            return vp
    return None
