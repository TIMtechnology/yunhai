"""精选观景点 DEM 快照：落盘后可跳过 Open-Meteo Elevation API。"""

from __future__ import annotations

import json
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from app.config import settings
from app.engine.solar import sunrise_azimuth_deg

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
TERRAIN_DIR = _PROJECT_ROOT / "data" / "terrain"
BAKED_VERSION = "v1"


def terrain_dir() -> Path:
    configured = getattr(settings, "terrain_snapshots_dir", "") or ""
    if configured.strip():
        path = Path(configured.strip())
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
    else:
        path = TERRAIN_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_path(spot_id: str, viewpoint_id: str) -> Path:
    safe = f"{spot_id}__{viewpoint_id}".replace("/", "_")
    return terrain_dir() / f"{safe}.json"


def list_baked_snapshots() -> list[Path]:
    return sorted(terrain_dir().glob("*.json"))


def load_snapshot(spot_id: str, viewpoint_id: str) -> dict[str, Any] | None:
    path = snapshot_path(spot_id, viewpoint_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("version") != BAKED_VERSION:
        return None
    return data


def save_snapshot(
    *,
    spot_id: str,
    viewpoint_id: str,
    lat: float,
    lng: float,
    elevation: float | None,
    base: dict[str, Any],
    profiles: dict[str, list[dict[str, Any]]],
) -> Path:
    path = snapshot_path(spot_id, viewpoint_id)
    payload = {
        "version": BAKED_VERSION,
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "lat": lat,
        "lng": lng,
        "elevation": elevation,
        "base": base,
        "profiles": profiles,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _coords_match(snapshot: dict[str, Any], lat: float, lng: float, *, tol: float = 0.002) -> bool:
    return abs(float(snapshot.get("lat") or 0) - lat) <= tol and abs(float(snapshot.get("lng") or 0) - lng) <= tol


def _pick_profile(
    profiles: dict[str, list[dict[str, Any]]],
    azimuth_deg: float,
) -> list[dict[str, Any]] | None:
    if not profiles:
        return None
    bucket = int(round(azimuth_deg / 5.0) * 5) % 360
    key = str(bucket)
    if key in profiles:
        return profiles[key]
    if "default" in profiles:
        return profiles["default"]
    nearest = min(profiles.keys(), key=lambda k: min(abs(int(k) - bucket), 360 - abs(int(k) - bucket)))
    return profiles[nearest]


def resolve_baked_terrain(
    *,
    spot_id: str | None,
    viewpoint_id: str | None,
    lat: float,
    lng: float,
    profile_date: date_cls,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    """返回 (base_grid, elev_profile) 或 (None, None)。"""
    if not spot_id or not viewpoint_id:
        return None, None
    snapshot = load_snapshot(spot_id, viewpoint_id)
    if not snapshot or not _coords_match(snapshot, lat, lng):
        return None, None
    az = sunrise_azimuth_deg(lat, lng, profile_date)
    profile = _pick_profile(snapshot.get("profiles") or {}, az)
    base = snapshot.get("base")
    if not isinstance(base, dict) or not profile:
        return None, None
    return dict(base), list(profile)


def preload_snapshots_to_cache() -> int:
    """启动时将落盘 DEM 写入 Redis/内存缓存。"""
    from app.adapters.dem import _azimuth_cache_bucket
    from app.services.cache import cache_set

    count = 0
    for path in list_baked_snapshots():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        lat = float(data.get("lat") or 0)
        lng = float(data.get("lng") or 0)
        base = data.get("base")
        if isinstance(base, dict):
            cache_set(f"terrain:v0:{lat:.4f}:{lng:.4f}", base, ttl=86400 * 30)
            count += 1
        for bucket_key, profile in (data.get("profiles") or {}).items():
            if not isinstance(profile, list):
                continue
            try:
                az_bucket = int(bucket_key)
            except ValueError:
                continue
            cache_set(
                f"terrain:sunrise_profile:v2:{lat:.4f}:{lng:.4f}:{az_bucket}:2.5",
                profile,
                ttl=86400 * 30,
            )
    return count
