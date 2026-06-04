#!/usr/bin/env python3
"""将社区点 cs_9e063149 合并到精选 donglingshan/fengding，保留全部标注。"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

COMMUNITY_LOC = "cs_9e063149"
CURATED_SPOT = "donglingshan"
VIEWPOINT = "fengding"
OLD_SPOT = "community"


def merge(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    before = conn.execute(
        """
        SELECT COUNT(*) AS n FROM cloudsea_labels
        WHERE spot_id=? AND viewpoint_id=?
        """,
        (OLD_SPOT, COMMUNITY_LOC),
    ).fetchone()["n"]
    print(f"待迁移标注: {before} 条 (community/{COMMUNITY_LOC})")

    conflicts = conn.execute(
        """
        SELECT l1.date, l1.window_start, l1.window_end
        FROM cloudsea_labels l1
        JOIN cloudsea_labels l2
          ON l1.date = l2.date
         AND l1.window_start = l2.window_start
         AND l1.window_end = l2.window_end
        WHERE l1.spot_id=? AND l1.viewpoint_id=?
          AND l2.spot_id=? AND l2.viewpoint_id=?
        """,
        (OLD_SPOT, COMMUNITY_LOC, CURATED_SPOT, VIEWPOINT),
    ).fetchall()
    if conflicts:
        print(f"冲突 {len(conflicts)} 条，删除目标侧重复后再迁移")
        for row in conflicts:
            conn.execute(
                """
                DELETE FROM cloudsea_labels
                WHERE spot_id=? AND viewpoint_id=? AND date=? AND window_start=? AND window_end=?
                """,
                (CURATED_SPOT, VIEWPOINT, row["date"], row["window_start"], row["window_end"]),
            )

    conn.execute(
        """
        UPDATE cloudsea_labels
        SET spot_id=?, viewpoint_id=?
        WHERE spot_id=? AND viewpoint_id=?
        """,
        (CURATED_SPOT, VIEWPOINT, OLD_SPOT, COMMUNITY_LOC),
    )

    meteo = conn.execute(
        """
        SELECT COUNT(*) AS n FROM meteo_hourly
        WHERE spot_id=? AND viewpoint_id=?
        """,
        (OLD_SPOT, COMMUNITY_LOC),
    ).fetchone()["n"]
    if meteo:
        conn.execute(
            """
            UPDATE meteo_hourly
            SET spot_id=?, viewpoint_id=?
            WHERE spot_id=? AND viewpoint_id=?
            """,
            (CURATED_SPOT, VIEWPOINT, OLD_SPOT, COMMUNITY_LOC),
        )
        print(f"已迁移 meteo_hourly: {meteo} 行")

    conn.execute(
        """
        UPDATE community_locations
        SET curated_spot_id=?, name='东灵山', updated_at=datetime('now')
        WHERE id=?
        """,
        (CURATED_SPOT, COMMUNITY_LOC),
    )

    conn.commit()

    after = conn.execute(
        """
        SELECT COUNT(*) AS n FROM cloudsea_labels
        WHERE spot_id=? AND viewpoint_id=?
        """,
        (CURATED_SPOT, VIEWPOINT),
    ).fetchone()["n"]
    leftover = conn.execute(
        """
        SELECT COUNT(*) AS n FROM cloudsea_labels
        WHERE spot_id=? AND viewpoint_id=?
        """,
        (OLD_SPOT, COMMUNITY_LOC),
    ).fetchone()["n"]
    loc = conn.execute(
        "SELECT name, curated_spot_id FROM community_locations WHERE id=?",
        (COMMUNITY_LOC,),
    ).fetchone()
    conn.close()

    print(f"迁移完成: donglingshan/fengding 现有 {after} 条标注")
    print(f"残留 community 键: {leftover} 条")
    if loc:
        print(f"社区点: {loc['name']} -> curated_spot_id={loc['curated_spot_id']}")
    if after < before:
        raise SystemExit(f"标注数量异常: 期望至少 {before}，实际 {after}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="data/cloudsea/cloudsea.db",
        help="cloudsea SQLite 路径",
    )
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")
    merge(db_path)


if __name__ == "__main__":
    main()
