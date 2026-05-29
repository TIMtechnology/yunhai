#!/usr/bin/env python3
"""回填 cloudsea.db 中 meteo_hourly 的完整垂直场特征（700hPa / 逆温 / 高云）。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.open_meteo import HOURLY_VARS  # noqa: E402
from app.engine.cloudsea_features import build_meteo_hour_row, meteo_row_complete  # noqa: E402

LAT, LNG = 41.31976, 125.40773
WINDOW_START, WINDOW_END = 3, 7


def fetch_day_hourly(day: str) -> dict:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast?"
        f"latitude={LAT}&longitude={LNG}&start_date={day}&end_date={day}"
        f"&hourly={','.join(HOURLY_VARS)}&timezone=Asia%2FShanghai"
    )
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp).get("hourly", {})


def upsert_meteo(conn: sqlite3.Connection, *, ts: str, raw: dict, spot_id: str, viewpoint_id: str) -> None:
    conn.execute(
        """
        INSERT INTO meteo_hourly
        (spot_id, viewpoint_id, lat, lng, elevation, ts, source, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(lat, lng, ts, source) DO UPDATE SET
            raw_json=excluded.raw_json,
            spot_id=excluded.spot_id,
            viewpoint_id=excluded.viewpoint_id
        """,
        (
            spot_id,
            viewpoint_id,
            LAT,
            LNG,
            804.0,
            ts,
            "historical_forecast",
            json.dumps(raw, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument("--sleep", type=float, default=0.3, help="API 请求间隔秒数")
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    labels = conn.execute(
        "SELECT DISTINCT date, spot_id, viewpoint_id FROM cloudsea_labels ORDER BY date"
    ).fetchall()

    updated = skipped = 0
    for label in labels:
        day = label["date"]
        spot_id = label["spot_id"] or "wunvshan"
        viewpoint_id = label["viewpoint_id"] or "dianjiangtai"
        existing = conn.execute(
            "SELECT ts, raw_json FROM meteo_hourly WHERE ts LIKE ? ORDER BY ts",
            (f"{day}T%",),
        ).fetchall()
        existing_rows = [json.loads(r["raw_json"]) for r in existing]
        if existing_rows and all(meteo_row_complete(r) for r in existing_rows):
            skipped += 1
            continue

        print(f"fetch {day} ...")
        hourly = fetch_day_hourly(day)
        times = hourly.get("time", [])
        for idx, t in enumerate(times):
            hour = int(t[11:13])
            if hour < WINDOW_START or hour >= WINDOW_END:
                continue
            raw = build_meteo_hour_row(hourly, idx)
            upsert_meteo(
                conn,
                ts=t,
                raw=raw,
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
            )
            updated += 1
        conn.commit()
        time.sleep(args.sleep)

    conn.close()
    print(f"完成: 更新 {updated} 条 hourly 行, 跳过 {skipped} 个已完整日期")


if __name__ == "__main__":
    main()
