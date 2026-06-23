#!/usr/bin/env python3
"""回填标注日的 operational 预报 archive（D-1 18:00 issue，precursor 窗）。"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.open_meteo import HOURLY_VARS  # noqa: E402
from app.engine.cloudsea_features import build_meteo_hour_row  # noqa: E402
from app.services.cloudsea_store import (  # noqa: E402
    default_forecast_issue_time,
    forecast_archive_precursor_complete,
    init_store,
    save_forecast_archive_rows,
)
from app.services.meteo_backfill import (  # noqa: E402
    precursor_hour_keys,
    resolve_label_coords,
)


def fetch_range_hourly_api(start: str, end: str, lat: float, lng: float) -> dict:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lng}&start_date={start}&end_date={end}"
        f"&hourly={','.join(HOURLY_VARS)}&timezone=Asia%2FShanghai"
    )
    with urllib.request.urlopen(url, timeout=90) as resp:
        return json.load(resp)


def extract_precursor_rows(payload: dict, target_date: str) -> list[dict]:
    keys = set(precursor_hour_keys(target_date))
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    rows: list[dict] = []
    for idx, t_str in enumerate(times):
        if str(t_str) not in keys:
            continue
        rows.append(build_meteo_hour_row(hourly, idx))
    return rows


def backfill_one_label(
    label: dict,
    *,
    db_path: Path,
    force: bool = False,
    sleep_sec: float = 0.35,
) -> dict:
    spot_id = str(label.get("spot_id") or "")
    viewpoint_id = str(label.get("viewpoint_id") or "")
    target_date = str(label.get("date") or "")
    if not spot_id or not viewpoint_id or not target_date:
        return {"status": "skip", "reason": "缺少 spot/viewpoint/date"}

    issue_time = default_forecast_issue_time(target_date)
    if not force and forecast_archive_precursor_complete(
        spot_id, viewpoint_id, target_date, issue_time=issue_time, db_path=db_path
    ):
        return {"status": "skipped", "date": target_date}

    d = date.fromisoformat(target_date)
    start = (d - timedelta(days=1)).isoformat()
    end = target_date
    lat, lng, _ = resolve_label_coords(label)
    try:
        payload = fetch_range_hourly_api(start, end, lat, lng)
    except Exception as exc:
        if sleep_sec > 0:
            time.sleep(sleep_sec)
        return {"status": "failed", "date": target_date, "error": str(exc)}

    rows = extract_precursor_rows(payload, target_date)
    if len(rows) < len(precursor_hour_keys(target_date)):
        return {
            "status": "failed",
            "date": target_date,
            "error": f"precursor 仅 {len(rows)}/{len(precursor_hour_keys(target_date))} 小时",
        }

    saved = save_forecast_archive_rows(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        target_date=target_date,
        issue_time=issue_time,
        rows=rows,
        db_path=db_path,
    )
    if sleep_sec > 0:
        time.sleep(sleep_sec)
    return {"status": "ok", "date": target_date, "hours_saved": saved}


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 meteo_forecast_archive（V2 operational）")
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.prod.db"))
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--spot-id")
    parser.add_argument("--viewpoint-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true", help="仅检查 archive 缺失")
    args = parser.parse_args()

    db_path = Path(args.db)
    os.environ["CLOUDSEA_DB_PATH"] = str(db_path.resolve())
    init_store()

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
        SELECT DISTINCT spot_id, viewpoint_id, date, lat, lng, location_id
        FROM cloudsea_labels
        WHERE {' AND '.join(clauses)}
        ORDER BY date
        """,
        params,
    ).fetchall()
    conn.close()

    if args.check:
        missing = []
        for raw in labels:
            spot_id = raw["spot_id"]
            viewpoint_id = raw["viewpoint_id"]
            target_date = raw["date"]
            if not forecast_archive_precursor_complete(
                spot_id, viewpoint_id, target_date, db_path=db_path
            ):
                missing.append(f"{target_date} {spot_id}/{viewpoint_id}")
        print(f"标注日 {len(labels)}，缺失 forecast archive {len(missing)}")
        for line in missing:
            print(f"  - {line}")
        return

    updated = skipped = failed = 0
    for raw in labels:
        result = backfill_one_label(dict(raw), db_path=db_path, force=args.force, sleep_sec=args.sleep)
        status = result.get("status")
        if status == "ok":
            updated += 1
            print(
                f"  ok {result['date']} {raw['spot_id']}/{raw['viewpoint_id']} "
                f"({result.get('hours_saved')}h)"
            )
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
            print(f"  fail {result.get('date')} {result.get('error') or result.get('reason')}")

    print(f"完成: 更新 {updated} 天, 跳过 {skipped} 天, 失败 {failed} 天")


if __name__ == "__main__":
    main()
