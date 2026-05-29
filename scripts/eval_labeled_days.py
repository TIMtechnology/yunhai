#!/usr/bin/env python3
"""用标注日回放规则预测，评估 viewing_mode + DEM 与人工标注贴合度。"""

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


def load_labels(db_path: Path, spot_id: str | None, viewpoint_id: str | None) -> list[dict]:
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
    rows = conn.execute(
        f"SELECT * FROM cloudsea_labels WHERE {' AND '.join(clauses)} ORDER BY date",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def label_bucket(status: str) -> str:
    if status in ("full", "partial"):
        return "has_cloudsea"
    return "none"


def pred_bucket(prob: int, *, peak_threshold: int = 55) -> str:
    return "has_cloudsea" if prob >= peak_threshold else "none"


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
    actual = label_bucket(label["status"])
    predicted = pred_bucket(peak_prob)
    loc = bt["prediction"]["location"]
    return {
        "date": label["date"],
        "status": label["status"],
        "peak_prob": peak_prob,
        "scenario": summary.get("scenario"),
        "actual": actual,
        "predicted": predicted,
        "match": actual == predicted,
        "viewing_mode": loc.get("viewing_mode"),
        "elev_max_5km": (loc.get("terrain") or {}).get("elev_max_5km_m"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.prod.db"))
    parser.add_argument("--spot-id")
    parser.add_argument("--viewpoint-id")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lng", type=float)
    parser.add_argument("--elevation", type=float)
    parser.add_argument("--name", default="评估点位")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"数据库不存在: {db_path}")
        print("请先运行: bash scripts/sync_cloudsea_db_from_prod.sh")
        sys.exit(1)

    labels = load_labels(db_path, args.spot_id, args.viewpoint_id)
    if not labels:
        print("无 approved 标注")
        sys.exit(0)

    from app.services.spot_loader import get_viewpoint

    results = []
    for label in labels:
        lat = args.lat or label.get("lat")
        lng = args.lng or label.get("lng")
        elev = args.elevation or label.get("elevation") or 804
        name = label.get("location_name") or args.name
        if lat is None or lng is None:
            vp = get_viewpoint(label["spot_id"], label["viewpoint_id"])
            if vp:
                lat, lng, elev = vp.lat, vp.lng, vp.elevation
        if lat is None or lng is None:
            print(f"skip {label['date']}: 无坐标")
            continue
        results.append(
            await eval_one(
                label,
                lat=float(lat),
                lng=float(lng),
                elev=float(elev),
                name=str(name),
            )
        )

    if not results:
        print("无有效评估日")
        return

    matches = sum(1 for r in results if r["match"])
    print(f"\n=== 标注回放评估 n={len(results)} 方向一致={matches}/{len(results)} ({matches/len(results):.0%}) ===")
    print(f"观云模式: {results[0].get('viewing_mode')} | 5km峰: {results[0].get('elev_max_5km')} m\n")
    for r in results:
        mark = "✓" if r["match"] else "✗"
        print(
            f"{mark} {r['date']} 标注={r['status']:8} 峰值={r['peak_prob']:3}% "
            f"预测={r['predicted']:12} 场景={r.get('scenario') or '-'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
