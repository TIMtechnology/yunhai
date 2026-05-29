#!/usr/bin/env python3
"""为精选观景点烘焙 DEM 快照（base 网格 + 全年日出方位剖面），写入 data/terrain/。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.dem import _azimuth_cache_bucket, fetch_sunrise_elevation_profile, get_terrain_context
from app.engine.solar import sunrise_azimuth_deg
from app.services.spot_loader import load_spots
from app.services.terrain_store import save_snapshot, snapshot_path


async def bake_viewpoint(spot_id: str, viewpoint_id: str, *, lat: float, lng: float, elevation: float) -> Path:
    ctx = await get_terrain_context(
        lat,
        lng,
        elevation=elevation,
        profile_date=date.today(),
        spot_id=None,
        viewpoint_id=None,
    )
    base_keys = {
        "lat",
        "lng",
        "source",
        "dem_version",
        "elev_viewpoint_m",
        "elev_open_meteo_m",
        "elev_max_1km_m",
        "elev_min_1km_m",
        "elev_max_5km_m",
        "elev_min_5km_m",
        "relief_1km_m",
        "relief_5km_m",
        "slope_deg",
        "aspect_deg",
        "sample_counts",
    }
    base = {k: ctx[k] for k in base_keys if k in ctx}
    base["source"] = "terrain_snapshot_baked"

    profiles: dict[str, list] = {}
    for month in range(1, 13):
        d = date(2025, month, 15)
        az = sunrise_azimuth_deg(lat, lng, d)
        bucket = str(_azimuth_cache_bucket(az))
        if bucket in profiles:
            continue
        profiles[bucket] = await fetch_sunrise_elevation_profile(lat, lng, azimuth_deg=az)
        print(f"  剖面 bucket={bucket}° (month={month}) points={len(profiles[bucket])}")

    path = save_snapshot(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        lat=lat,
        lng=lng,
        elevation=elevation,
        base=base,
        profiles=profiles,
    )
    return path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spot-id", help="仅烘焙指定 spot")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    spots = load_spots()
    tasks: list[tuple[str, str, float, float, float]] = []
    for spot in spots.values():
        if args.spot_id and spot.id != args.spot_id:
            continue
        for vp in spot.viewpoints:
            tasks.append((spot.id, vp.id, vp.lat, vp.lng, vp.elevation))

    if not tasks:
        print("无观景点可烘焙")
        return

    print(f"将烘焙 {len(tasks)} 个观景点 → {ROOT / 'data' / 'terrain'}")
    for spot_id, vp_id, lat, lng, elev in tasks:
        out = snapshot_path(spot_id, vp_id)
        print(f"\n[{spot_id}/{vp_id}] {lat:.4f},{lng:.4f} elev={elev}m")
        if args.dry_run:
            print(f"  → {out}")
            continue
        path = await bake_viewpoint(spot_id, vp_id, lat=lat, lng=lng, elevation=elev)
        print(f"  ✓ 写入 {path}")
        await asyncio.sleep(2.5)


if __name__ == "__main__":
    asyncio.run(main())
