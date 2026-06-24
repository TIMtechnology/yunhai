#!/usr/bin/env python3
"""按日期区间回放标注日，统计方向一致率与逐日明细。"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.prod.db"))
_pre_args, _ = _pre.parse_known_args()
os.environ["CLOUDSEA_DB_PATH"] = str(Path(_pre_args.db).resolve())
os.environ.setdefault("CLOUDSEA_ML_ENABLED", "1")

from app.models.schemas import PredictRequest  # noqa: E402
from app.services.cloudsea_store import init_store  # noqa: E402
from app.services.predictor import run_backtest_prediction, warm_location_caches  # noqa: E402
from app.services.spot_loader import get_viewpoint  # noqa: E402


def load_labels(
    db_path: Path,
    *,
    spot_id: str | None,
    viewpoint_id: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    clauses = ["(review_status='approved' OR review_status IS NULL)"]
    params: list[object] = []
    if spot_id:
        clauses.append("spot_id=?")
        params.append(spot_id)
    if viewpoint_id:
        clauses.append("viewpoint_id=?")
        params.append(viewpoint_id)
    if date_from:
        clauses.append("date>=?")
        params.append(date_from)
    if date_to:
        clauses.append("date<=?")
        params.append(date_to)
    rows = conn.execute(
        f"SELECT * FROM cloudsea_labels WHERE {' AND '.join(clauses)} ORDER BY date",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def label_positive(status: str) -> bool:
    return status in ("full", "partial")


async def eval_one(label: dict, *, lat: float, lng: float, elev: float, name: str) -> dict:
    req = PredictRequest(
        lat=lat,
        lng=lng,
        elevation=elev,
        name=name,
        spot_id=label["spot_id"],
        viewpoint_id=label["viewpoint_id"],
        hours=24,
    )
    bt = await run_backtest_prediction(req=req, target_date=label["date"])
    summary = bt.get("sunrise_window_summary") or {}
    peak_prob = int(summary.get("max_cloudsea_prob") or 0)
    actual_pos = label_positive(label["status"])
    pred_pos = peak_prob >= 55
    return {
        "date": label["date"],
        "status": label["status"],
        "peak_prob": peak_prob,
        "actual_pos": actual_pos,
        "pred_pos": pred_pos,
        "match": actual_pos == pred_pos,
        "scenario": summary.get("scenario"),
        "data_source": (bt.get("meta") or {}).get("data_source"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.prod.db"))
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument("--from", dest="date_from", default="2026-05-01")
    parser.add_argument("--to", dest="date_to", default="2026-06-30")
    parser.add_argument("--threshold", type=int, default=55)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()

    db_path = Path(args.db)
    init_store()
    labels = load_labels(
        db_path,
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    if not labels:
        print("无标注")
        return

    vp = get_viewpoint(args.spot_id, args.viewpoint_id)
    lat, lng, elev = vp.lat, vp.lng, vp.elevation or 804
    await warm_location_caches(
        lat=lat,
        lng=lng,
        elevation=elev,
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
    )

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def _one(lb: dict) -> dict:
        async with sem:
            return await eval_one(lb, lat=lat, lng=lng, elev=elev, name=f"{args.spot_id}")

    results = list(await asyncio.gather(*[_one(lb) for lb in labels]))
    matches = sum(1 for r in results if r["match"])
    fp = sum(1 for r in results if r["pred_pos"] and not r["actual_pos"])
    fn = sum(1 for r in results if not r["pred_pos"] and r["actual_pos"])

    print(
        f"\n=== {args.spot_id}/{args.viewpoint_id} "
        f"{args.date_from}~{args.date_to} n={len(results)} ==="
    )
    print(
        f"方向一致 {matches}/{len(results)} ({matches/len(results):.1%})  "
        f"FP={fp} FN={fn}  阈值={args.threshold}%"
    )
    for r in results:
        mark = "✓" if r["match"] else "✗"
        bucket = "有" if r["actual_pos"] else "无"
        print(
            f"{mark} {r['date']} 标注={r['status']:8}({bucket}) "
            f"P={r['peak_prob']:3}% 源={r.get('data_source') or '-'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
