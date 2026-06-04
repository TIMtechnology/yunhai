#!/usr/bin/env python3
"""撤销误推标注，并补全东灵山 5 月缺口（按用户抖音规则）。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

REMOVE = [
    ("wunvshan", "dianjiangtai", "2024-10-06"),
    ("wunvshan", "dianjiangtai", "2024-10-07"),
    ("wunvshan", "dianjiangtai", "2024-10-12"),
    ("wunvshan", "dianjiangtai", "2025-05-23"),
    ("wunvshan", "dianjiangtai", "2025-05-25"),
    ("wunvshan", "dianjiangtai", "2026-04-21"),
    ("donglingshan", "fengding", "2026-05-30"),
]

ADD_NONE = [
    ("donglingshan", "fengding", "2026-05-19"),
    ("donglingshan", "fengding", "2026-05-25"),
    ("donglingshan", "fengding", "2026-05-26"),
]

NOTE = "5月未发抖音·补标无日出云海"
UPDATE_NOTES_DATES = [
    f"2026-05-{d:02d}"
    for d in range(2, 13)
] + ["2026-05-15", "2026-05-16", "2026-05-18"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    db = Path(args.db)

    import os

    os.environ["CLOUDSEA_DB_PATH"] = str(db.resolve())
    from app.services.cloudsea_store import init_store, upsert_label

    init_store()
    conn = sqlite3.connect(db)
    removed = 0
    for spot, vp, date_key in REMOVE:
        cur = conn.execute(
            """
            DELETE FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=? AND date=?
              AND window_start=3 AND window_end=7
            """,
            (spot, vp, date_key),
        )
        removed += cur.rowcount
        if cur.rowcount:
            print(f"removed {spot}/{vp} {date_key}")
    conn.commit()
    conn.close()

    for spot, vp, date_key in ADD_NONE:
        upsert_label(
            spot_id=spot,
            viewpoint_id=vp,
            date_key=date_key,
            status="none",
            notes=NOTE,
            labeled_by="admin_batch",
            review_status="approved",
        )
        print(f"added {date_key}")

    conn = sqlite3.connect(db)
    for date_key in UPDATE_NOTES_DATES:
        conn.execute(
            """
            UPDATE cloudsea_labels SET notes=?, updated_at=datetime('now')
            WHERE spot_id='donglingshan' AND viewpoint_id='fengding' AND date=?
            """,
            (NOTE, date_key),
        )
    conn.commit()

    total = conn.execute(
        "SELECT COUNT(*) FROM cloudsea_labels WHERE spot_id='donglingshan' AND viewpoint_id='fengding'"
    ).fetchone()[0]
    may = conn.execute(
        "SELECT date, status FROM cloudsea_labels WHERE spot_id='donglingshan' AND viewpoint_id='fengding' AND date LIKE '2026-05-%' ORDER BY date"
    ).fetchall()
    conn.close()
    print(f"donglingshan total={total}, may_2026={len(may)}")
    missing = [f"2026-05-{d:02d}" for d in range(1, 30)] - [r[0] for r in may]
    if missing:
        print("WARNING still missing:", missing)
    else:
        print("OK: 2026-05-01..29 全覆盖")


if __name__ == "__main__":
    main()
