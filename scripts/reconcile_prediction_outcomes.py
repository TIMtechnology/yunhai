#!/usr/bin/env python3
"""对 prediction_access_log 写入次日回测 outcome。"""
from __future__ import annotations

import argparse
import sys
from datetime import date as date_cls, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.cloudsea_store import init_store  # noqa: E402
from app.services.prediction_feedback import reconcile_target_date  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="回测 prediction_access_log")
    parser.add_argument("--date", help="目标日出日 YYYY-MM-DD")
    parser.add_argument("--days-back", type=int, default=7, help="无 --date 时回测最近 N 天")
    parser.add_argument("--force", action="store_true", help="覆盖已有 outcome")
    args = parser.parse_args()

    init_store()
    if args.date:
        dates = [args.date]
    else:
        today = date_cls.today()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(1, args.days_back + 1)]

    for d in dates:
        result = reconcile_target_date(d, force=args.force)
        print(f"{d}: reconciled {result['reconciled']}/{result['total']}")


if __name__ == "__main__":
    main()
