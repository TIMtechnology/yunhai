#!/usr/bin/env python3
"""调试生产 watcher（可在容器内运行）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.services.cloudsea_store import init_store, list_watchlist_spots  # noqa: E402
from app.services.forecast_watch import run_forecast_watch_sync  # noqa: E402


def main() -> None:
    init_store()
    print(
        "settings",
        {
            "cloudsea_enabled": settings.cloudsea_enabled,
            "cloudsea_auto_snapshot": settings.cloudsea_auto_snapshot,
            "cloudsea_watch_enabled": settings.cloudsea_watch_enabled,
        },
    )
    print("watchlist", list_watchlist_spots(label_days=7))
    force = "--force" in sys.argv
    dry = "--dry-run" in sys.argv
    r = run_forecast_watch_sync(force=force, dry_run=dry)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
