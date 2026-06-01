#!/usr/bin/env python3
"""删除误推的标注（无 DELETE API 时用）。"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

REMOVE = [
    ("wunvshan", "dianjiangtai", "2024-10-06"),
    ("wunvshan", "dianjiangtai", "2024-10-07"),
    ("wunvshan", "dianjiangtai", "2024-10-12"),
    ("wunvshan", "dianjiangtai", "2025-05-23"),
    ("wunvshan", "dianjiangtai", "2025-05-25"),
    ("wunvshan", "dianjiangtai", "2026-04-21"),
    ("donglingshan", "fengding", "2026-05-30"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
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
    for spot, vp in [("wunvshan", "dianjiangtai"), ("donglingshan", "fengding")]:
        n = conn.execute(
            "SELECT COUNT(*) FROM cloudsea_labels WHERE spot_id=? AND viewpoint_id=?",
            (spot, vp),
        ).fetchone()[0]
        print(f"{spot}/{vp} total={n}")
    conn.close()
    print(f"done, removed {removed}")


if __name__ == "__main__":
    main()
