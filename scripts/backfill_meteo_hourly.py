#!/usr/bin/env python3
"""回填 cloudsea.db 中全部标注日的 meteo_hourly + meteo_day_cache（多点位，全日）。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.meteo_backfill import (  # noqa: E402
    backfill_all_labels,
    backfill_label_meteo,
    precursor_window_meteo_complete,
    sunrise_window_meteo_complete,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="回填标注日历史气象到 cloudsea.db")
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument("--sleep", type=float, default=0.35, help="API 请求间隔秒数")
    parser.add_argument("--spot-id", help="仅回填指定 spot")
    parser.add_argument("--viewpoint-id", help="配合 --spot-id 指定 viewpoint")
    parser.add_argument("--force", action="store_true", help="即使已完整也重新拉取")
    parser.add_argument("--check", action="store_true", help="仅检查缺失，不请求 API")
    args = parser.parse_args()

    db_path = Path(args.db)
    os.environ["CLOUDSEA_DB_PATH"] = str(db_path.resolve())

    if args.check:
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        clauses = ["spot_id IS NOT NULL", "viewpoint_id IS NOT NULL"]
        params: list = []
        if args.spot_id:
            clauses.append("spot_id=?")
            params.append(args.spot_id)
        if args.viewpoint_id:
            clauses.append("viewpoint_id=?")
            params.append(args.viewpoint_id)
        labels = conn.execute(
            f"""
            SELECT DISTINCT spot_id, viewpoint_id, date FROM cloudsea_labels
            WHERE {' AND '.join(clauses)} ORDER BY date
            """,
            params,
        ).fetchall()
        conn.close()
        missing_sunrise = [
            f"{r['date']} {r['spot_id']}/{r['viewpoint_id']}"
            for r in labels
            if not sunrise_window_meteo_complete(
                r["spot_id"], r["viewpoint_id"], r["date"], db_path=db_path
            )
        ]
        missing_precursor = [
            f"{r['date']} {r['spot_id']}/{r['viewpoint_id']}"
            for r in labels
            if not precursor_window_meteo_complete(
                r["spot_id"], r["viewpoint_id"], r["date"], db_path=db_path
            )
        ]
        print(f"标注日 {len(labels)}，缺失日出窗 {len(missing_sunrise)}，缺失 precursor {len(missing_precursor)}")
        if missing_sunrise:
            print("日出窗(03-06)缺失:")
            for line in missing_sunrise:
                print(f"  - {line}")
        if missing_precursor:
            print("precursor(D-1 20:00→D 07:00)缺失:")
            for line in missing_precursor:
                print(f"  - {line}")
        return

    stats = backfill_all_labels(
        db_path,
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
        force=args.force,
        sleep_sec=args.sleep,
    )
    print(
        f"完成: 更新 {stats['updated']} 天, "
        f"跳过 {stats['skipped']} 天已完整, 失败 {stats['failed']} 天"
    )


if __name__ == "__main__":
    main()
