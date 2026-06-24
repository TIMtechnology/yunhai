#!/usr/bin/env python3
"""分析 prediction_access_log：预报演变 + ML/规则问题诊断。"""
from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.cloudsea_features import aggregate_v7_features, _segment_for_row
from app.engine.cloudsea_ml import merge_ml_cloudsea_score, predict_day_cloudsea
from app.engine.cloudsea_scorer import score_cloudsea
from app.services.meteo_backfill import load_label_precursor_meteo, precursor_hour_keys

POSITIVE = 50


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _seg_stats(rows: list[dict], target_date: str, seg: str) -> dict:
    seg_rows = [r for r in rows if _segment_for_row(r, target_date) == seg]
    if not seg_rows:
        return {}
    rh = [float(r["rh"]) for r in seg_rows if r.get("rh") is not None]
    low = [float(r["cloud_low"]) for r in seg_rows if r.get("cloud_low") is not None]
    return {
        "rh_mean": round(sum(rh) / len(rh), 1) if rh else None,
        "cloud_low_mean": round(sum(low) / len(low), 1) if low else None,
        "n": len(seg_rows),
    }


def _replay_scores(
    forecast_rows: list[dict],
    target_date: str,
    *,
    spot_id: str,
    viewpoint_id: str,
    elevation: float = 804.0,
) -> dict:
    dawn = [r for r in forecast_rows if _segment_for_row(r, target_date) == "dawn"]
    ml = predict_day_cloudsea(
        dawn,
        elevation=elevation,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        target_date=target_date,
        precursor_rows=forecast_rows,
    )
    rule_probs: list[int] = []
    for row in dawn:
        rs = score_cloudsea(
            rh=float(row.get("rh") or 70),
            cloud_low=float(row.get("cloud_low") or 0),
            cloud_mid=float(row.get("cloud_mid") or 0),
            cloud_high=float(row.get("cloud_high") or 0),
            wind=float(row.get("wind_speed") or 2),
            precip=float(row.get("precipitation") or 0),
            precip_recent=float(row.get("precipitation") or 0),
            temp=float(row.get("temp") or 15),
            dewpoint=float(row.get("dewpoint") or 10),
            elevation=elevation,
            month=int(target_date[5:7]),
            rh_850=float(row.get("rh_850") or 70),
            rh_700=float(row.get("rh_700") or 50),
            t_850=float(row.get("t_850") or 0),
            t_925=float(row.get("t_925") or 0),
            visibility=float(row.get("visibility") or 10000),
        )
        rule_probs.append(int(rs.probability))
    rule_peak = max(rule_probs) if rule_probs else None
    ml_prob = int(ml.probability) if ml else None
    fused_peak = None
    if ml and rule_peak is not None and dawn:
        dawn_peak_idx = rule_probs.index(rule_peak)
        peak_row = dawn[dawn_peak_idx]
        fuzzy = score_cloudsea(
            rh=float(peak_row.get("rh") or 70),
            cloud_low=float(peak_row.get("cloud_low") or 0),
            cloud_mid=float(peak_row.get("cloud_mid") or 0),
            cloud_high=float(peak_row.get("cloud_high") or 0),
            wind=float(peak_row.get("wind_speed") or 2),
            precip=float(peak_row.get("precipitation") or 0),
            precip_recent=float(peak_row.get("precipitation") or 0),
            temp=float(peak_row.get("temp") or 15),
            dewpoint=float(peak_row.get("dewpoint") or 10),
            elevation=elevation,
            month=int(target_date[5:7]),
            rh_850=float(peak_row.get("rh_850") or 70),
            rh_700=float(peak_row.get("rh_700") or 50),
            t_850=float(peak_row.get("t_850") or 0),
            t_925=float(peak_row.get("t_925") or 0),
            visibility=float(peak_row.get("visibility") or 10000),
        )
        fused = merge_ml_cloudsea_score(fuzzy, ml, spot_id=spot_id, viewpoint_id=viewpoint_id)
        fused_peak = int(fused.probability)
    v7_feat = aggregate_v7_features(forecast_rows, target_date=target_date, elevation=elevation)
    return {
        "rule_peak": rule_peak,
        "ml_prob": ml_prob,
        "fused_replay": fused_peak,
        "delta_rh_night_dawn": round(
            (v7_feat.get("dawn_rh_mean") or 0) - (v7_feat.get("night_rh_mean") or 0), 1
        ),
        "dawn_rh_mean": v7_feat.get("dawn_rh_mean"),
        "dawn_cloud_low_mean": v7_feat.get("dawn_cloud_low_mean"),
        "night_rh_mean": v7_feat.get("night_rh_mean"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data/cloudsea/cloudsea.prod.db"))
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument("--date", default="2026-06-24")
    args = parser.parse_args()
    db = Path(args.db)
    if not db.is_file():
        print(f"DB not found: {db}", file=sys.stderr)
        sys.exit(1)

    conn = _connect(db)
    logs = conn.execute(
        """
        SELECT l.*, o.diagnosis_json, o.direction_ok, o.forecast_error_json
        FROM prediction_access_log l
        LEFT JOIN prediction_access_outcome o ON o.access_log_id = l.id
        WHERE l.spot_id=? AND l.viewpoint_id=? AND l.target_date=?
        ORDER BY l.created_at
        """,
        (args.spot_id, args.viewpoint_id, args.date),
    ).fetchall()
    conn.close()

    actual = load_label_precursor_meteo(args.spot_id, args.viewpoint_id, args.date, db_path=db)
    actual_stats = {s: _seg_stats(actual, args.date, s) for s in ("evening", "night", "dawn")}
    label = None
    conn = _connect(db)
    lb = conn.execute(
        "SELECT status FROM cloudsea_labels WHERE spot_id=? AND viewpoint_id=? AND date=?",
        (args.spot_id, args.viewpoint_id, args.date),
    ).fetchone()
    conn.close()
    if lb:
        label = lb["status"]

    print(f"=== {args.spot_id}/{args.viewpoint_id} · 目标日 {args.date} · 标注 {label or '?'} ===")
    print(f"访问快照 {len(logs)} 条 · precursor 实况 segments: {actual_stats}\n")

    prev_fp = None
    snapshots: list[dict] = []

    for row in logs:
        pred = json.loads(row["prediction_json"])
        meteo = json.loads(row["meteo_snapshot_json"])
        rows = meteo.get("rows") or []
        fp = meteo.get("fingerprint") or ""
        visits = pred.get("access_visits") or [{"at": row["created_at"], "peak_cloudsea_prob": pred.get("peak_cloudsea_prob")}]
        stored_peak = pred.get("peak_cloudsea_prob")
        replay = _replay_scores(rows, args.date, spot_id=args.spot_id, viewpoint_id=args.viewpoint_id)
        seg = {s: _seg_stats(rows, args.date, s) for s in ("evening", "night", "dawn")}
        fp_changed = prev_fp is not None and fp != prev_fp
        prev_fp = fp

        item = {
            "id": row["id"],
            "created_at": row["created_at"][:16],
            "lead_h": row["lead_hours_to_dawn"],
            "visits": len(visits),
            "stored_P": stored_peak,
            "replay": replay,
            "fp_changed": fp_changed,
            "segments": seg,
            "fingerprint": fp[:8],
        }
        snapshots.append(item)

        print(f"--- log#{row['id']}  {item['created_at']}  lead={item['lead_h']:.1f}h  visits={item['visits']}  fp={'Δ' if fp_changed else '=' if prev_fp else '·'} ---")
        print(f"  线上 P={stored_peak}%  |  回放: 规则峰值={replay['rule_peak']}%  ML日={replay['ml_prob']}%  融合≈{replay['fused_replay']}%")
        print(f"  v7: night RH={replay['night_rh_mean']} → dawn RH={replay['dawn_rh_mean']} (Δ{replay['delta_rh_night_dawn']:+.0f})  dawn低云={replay['dawn_cloud_low_mean']}")
        for s in ("evening", "night", "dawn"):
            f, a = seg.get(s, {}), actual_stats.get(s, {})
            print(f"  {s:7s} 预报RH={f.get('rh_mean')} 实况RH={a.get('rh_mean')}  |  预报低云={f.get('cloud_low_mean')} 实况低云={a.get('cloud_low_mean')}")
        diag = row["diagnosis_json"]
        if diag:
            d = json.loads(diag)
            print(f"  诊断: {d.get('summary')}")
        print()

    # Forecast drift: compare first vs last unique fingerprints
    unique_fps: dict[str, dict] = {}
    for s in snapshots:
        fp = s["fingerprint"]
        if fp not in unique_fps:
            unique_fps[fp] = s

    print("=== 预报版本数（不同 fingerprint）:", len(unique_fps), "===")
    for fp, s in unique_fps.items():
        print(f"  fp={fp}…  首次 {s['created_at']}  P={s['stored_P']}%  dawn RH={s['replay']['dawn_rh_mean']}")

    # Dawn hour-by-hour drift across snapshots
    print("\n=== 日出窗 03–07 点 RH 预报演变（各快照） ===")
    dawn_keys = [k for k in precursor_hour_keys(args.date) if "T03:" <= k.split("T")[1] or k.startswith(f"{args.date}T0") and int(k[11:13]) < 8]
    dawn_keys = sorted(k for k in precursor_hour_keys(args.date) if k.startswith(args.date) and 3 <= int(k[11:13]) < 8)
    actual_by = {str(r["time"]): r for r in actual}
    header = "time".ljust(8) + "".join(f"log#{s['id']}".rjust(8) for s in snapshots) + "  actual"
    print(header)
    for ts in dawn_keys:
        line = ts[11:16].ljust(8)
        for row in logs:
            meteo = json.loads(row["meteo_snapshot_json"])
            by_t = {str(r["time"]): r for r in meteo.get("rows") or []}
            r = by_t.get(ts)
            line += (f"{r['rh']:.0f}" if r and r.get("rh") is not None else "—").rjust(8)
        a = actual_by.get(ts)
        a_rh = f"{a['rh']:.0f}" if a and a.get("rh") is not None else "—"
        line += f"  {a_rh}"
        print(line)

    print("\n=== 结论摘要 ===")
    false_pos = [s for s in snapshots if (s["stored_P"] or 0) >= POSITIVE]
    print(f"虚高快照( P≥{POSITIVE}% ): {len(false_pos)}/{len(snapshots)}")
    if snapshots:
        last = snapshots[-1]
        first = snapshots[0]
        print(f"P 变化: {first['stored_P']}% (lead {first['lead_h']:.0f}h) → {last['stored_P']}% (lead {last['lead_h']:.0f}h)")


if __name__ == "__main__":
    main()
