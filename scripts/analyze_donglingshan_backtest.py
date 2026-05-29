#!/usr/bin/env python3
"""东灵山标注日深度回放：对比单点 vs 扇区多点气象，定位规则误判原因。"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.schemas import PredictRequest  # noqa: E402
from app.services.predictor import run_backtest_prediction  # noqa: E402


def load_labels(db: Path, spot_id: str, vp_id: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM cloudsea_labels
        WHERE spot_id=? AND viewpoint_id=?
          AND (review_status='approved' OR review_status IS NULL)
        ORDER BY date
        """,
        (spot_id, vp_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


async def analyze_one(label: dict) -> dict:
    req = PredictRequest(
        lat=40.0161,
        lng=115.50136,
        elevation=2274,
        name="东灵山",
        spot_id=label["spot_id"],
        viewpoint_id=label["viewpoint_id"],
        hours=24,
    )
    bt = await run_backtest_prediction(req=req, target_date=label["date"])
    loc = bt["prediction"]["location"]
    obs = loc.get("observable") or {}
    summary = bt.get("sunrise_window_summary") or {}
    feat = summary.get("features_at_peak") or {}
    return {
        "date": label["date"],
        "status": label["status"],
        "peak_prob": summary.get("max_cloudsea_prob"),
        "obs_frac": obs.get("observable_fraction"),
        "sector_low": obs.get("sector_cloud_low_mean"),
        "sector_pts": obs.get("sector_meteo_points"),
        "summit_low": feat.get("cloud_low"),
        "rh_850": feat.get("rh_850"),
        "precip48": feat.get("precip48"),
        "scenario": summary.get("scenario"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data/cloudsea/cloudsea.prod.db"))
    args = parser.parse_args()
    labels = load_labels(Path(args.db), "donglingshan", "fengding")
    if not labels:
        labels = load_labels(Path(args.db), "community", "cs_9e063149")
    rows = [await analyze_one(l) for l in labels]
    print("\n=== 东灵山标注深度分析 ===")
    print(f"{'日期':<12} {'标注':<8} {'峰值%':>5} {'可观测':>6} {'扇区低云':>8} {'峰顶低云':>8} {'RH850':>6} 场景")
    for r in rows:
        print(
            f"{r['date']:<12} {r['status']:<8} {r['peak_prob']:>5} "
            f"{(r['obs_frac'] or 0):>5.0%} "
            f"{(r['sector_low'] if r['sector_low'] is not None else 0):>7.0f}% "
            f"{(r['summit_low'] if r['summit_low'] is not None else 0):>7.0f}% "
            f"{(r['rh_850'] if r['rh_850'] is not None else 0):>5.0f} "
            f"{r['scenario'] or '-'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
