#!/usr/bin/env python3
"""对比典型点位的 DEM 地形上下文（CLI）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.dem import get_terrain_context  # noqa: E402

SPOTS = [
    ("五女山·点将台", 41.31976, 125.40773, 804, 600, 40, 25),
    ("大黑山·主峰", 39.09811, 121.78396, 663, 500, 35, 20),
    ("东灵山·峰顶", 40.0201, 115.4785, 2303, 1500, 30, 15),
    ("黄丫口", 40.633, 122.509, 200, 400, 50, 30),
]


async def main() -> None:
    rows = []
    for name, lat, lng, elev, cloud_base, cloud_low, cloud_mid in SPOTS:
        ctx = await get_terrain_context(
            lat,
            lng,
            elevation=elev,
            cloud_base_m=cloud_base,
            cloud_low_pct=cloud_low,
            cloud_mid_pct=cloud_mid,
        )
        layer = ctx.get("cloud_layer") or {}
        rows.append(
            {
                "name": name,
                "viewing_mode": ctx["viewing_mode"],
                "elev_view_m": ctx["elev_viewpoint_m"],
                "elev_max_1km_m": ctx["elev_max_1km_m"],
                "relief_5km_m": ctx["relief_5km_m"],
                "elev_max_5km_m": ctx["elev_max_5km_m"],
                "cloud_layer": layer.get("layer_label", "—"),
                "valley_fill": layer.get("valley_fill_potential"),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
