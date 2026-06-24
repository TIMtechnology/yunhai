#!/usr/bin/env python3
"""重点点位气象 watcher：预报变化且无用户访问时主动跑 scheduled predict。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
_pre_args, _ = _pre.parse_known_args()
os.environ["CLOUDSEA_DB_PATH"] = str(Path(_pre_args.db).resolve())
os.environ.setdefault("CLOUDSEA_ENABLED", "1")
os.environ.setdefault("CLOUDSEA_AUTO_SNAPSHOT", "1")

from app.services.cloudsea_store import init_store  # noqa: E402
from app.services.forecast_watch import run_forecast_watch_sync  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="气象变化 watcher → scheduled predict")
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument("--label-days", type=int, default=None, help="watchlist：近 N 日有标注")
    parser.add_argument("--spot-id", help="仅监控指定 spot")
    parser.add_argument("--viewpoint-id", help="配合 --spot-id")
    parser.add_argument("--force", action="store_true", help="忽略活跃窗/用户静默/变化阈值")
    parser.add_argument("--dry-run", action="store_true", help="只打印决策，不调用 predict")
    args = parser.parse_args()

    os.environ["CLOUDSEA_DB_PATH"] = str(Path(args.db).resolve())
    init_store()

    if args.spot_id and not args.viewpoint_id:
        parser.error("--spot-id 需配合 --viewpoint-id")
    if args.viewpoint_id and not args.spot_id:
        parser.error("--viewpoint-id 需配合 --spot-id")

    summary = run_forecast_watch_sync(
        label_days=args.label_days,
        force=args.force,
        dry_run=args.dry_run,
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
