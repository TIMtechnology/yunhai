#!/usr/bin/env python3
"""五女山全部「有云海」标注日：DB 气象 + 本地回测峰值（不调用 HTTP API）。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.engine.cloudsea_features import TRIPLET_DISCRIM_FEATURE_NAMES, aggregate_day_features
from app.engine.ml_eligibility import sunrise_window_rain_summary
from app.services.meteo_backfill import load_label_sunrise_meteo, resolve_label_coords
from train_cloudsea_model import load_dataset  # noqa: E402


def load_full_labels(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM cloudsea_labels
        WHERE spot_id='wunvshan' AND viewpoint_id='dianjiangtai'
          AND status IN ('full', 'partial')
        ORDER BY date
        """
    ).fetchall()
    return [dict(r) for r in rows]


def sunrise_meteo_summary(db: Path, date_key: str) -> dict | None:
    rows = load_label_sunrise_meteo("wunvshan", "dianjiangtai", date_key, db_path=db)
    if not rows:
        return None
    if sunrise_window_rain_summary(rows)["has_rain"]:
        return {"rain": True}
    peak = min(rows, key=lambda r: float(r.get("visibility") or 99999))
    return {
        "rain": False,
        "n_hours": len(rows),
        "vis_min_m": float(min(float(r.get("visibility") or 99999) for r in rows)),
        "vis_peak_m": float(peak.get("visibility") or 0),
        "rh_mean": sum(float(r.get("rh") or 0) for r in rows) / len(rows),
        "rh850_mean": sum(float(r.get("rh_850") or 0) for r in rows) / len(rows),
        "cloud_mid_mean": sum(float(r.get("cloud_mid") or 0) for r in rows) / len(rows),
        "cloud_low_mean": sum(float(r.get("cloud_low") or 0) for r in rows) / len(rows),
        "inversion_mean": sum(float(r.get("inversion") or 0) for r in rows) / len(rows),
    }


def loocv_for_dates(X, y, meta, fn, dates: set[str]) -> dict[str, float]:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import LeaveOneOut
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    probs: dict[str, float] = {}
    for tr, te in LeaveOneOut().split(X):
        d = meta[te[0]]["date"]
        if d not in dates:
            continue
        m = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=0.3, class_weight="balanced", max_iter=3000, random_state=42
                    ),
                ),
            ]
        )
        m.fit(X[tr], y[tr])
        probs[d] = float(m.predict_proba(X[te])[:, 1][0])
    return probs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    args = parser.parse_args()
    db = Path(args.db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    labels = load_full_labels(conn)

    X, y, meta, fn = load_dataset(
        db, spot_id="wunvshan", viewpoint_id="dianjiangtai", db_only=True
    )
    import pickle
    from app.engine.ml_eligibility import spot_model_path

    model_path = spot_model_path("wunvshan", "dianjiangtai", models_dir=ROOT / "data" / "cloudsea" / "models")
    for candidate in (
        model_path,
        Path("/app/models/spot_wunvshan_dianjiangtai.pkl"),
        ROOT / "data" / "cloudsea" / "models" / "spot_wunvshan_dianjiangtai.pkl",
    ):
        if candidate.is_file():
            model_path = candidate
            break
    artifact = pickle.load(open(model_path, "rb")) if model_path.is_file() else None
    clf = artifact["model"] if artifact else None
    feat_names = artifact.get("feature_names", fn) if artifact else fn

    full_dates = {lb["date"] for lb in labels}
    loo = loocv_for_dates(X, y, meta, fn, full_dates)

    def feat(date: str, key: str) -> float:
        i = next(j for j, m in enumerate(meta) if m["date"] == date)
        return X[i][fn.index(key)] if key in fn else float("nan")

    def full_p(date: str) -> float | None:
        if clf is None:
            return None
        i = next(j for j, m in enumerate(meta) if m["date"] == date)
        row = [[X[i][feat_names.index(n)] if n in feat_names else 0.0 for n in feat_names]]
        return float(clf.predict_proba(row)[0, 1] * 100)

    print(f"DB: {db}")
    print(f"模型: {model_path.name if model_path.is_file() else '无'}")
    print(f"有云海标注 {len(labels)} 天 | 训练有效样本 {len(meta)} 天\n")

    print(f"{'日期':<12} {'标':<4} {'峰值%':>6} {'LOOCV%':>7} {'vis_m':>7} {'rh':>4} {'rh850':>5} {'fog_h':>5} {'dry_lv':>6} {'备注'}")
    print("-" * 95)

    for lb in labels:
        d = lb["date"]
        met = sunrise_meteo_summary(db, d)
        if met is None:
            met = {"rain": False, "vis_min_m": 0, "rh_mean": 0, "rh850_mean": 0}
        if met.get("rain"):
            print(f"{d:<12} {lb['status']:<4} {'—':>6} {'—':>7} {'降水':>7}")
            continue
        if d not in {m["date"] for m in meta}:
            print(f"{d:<12} {lb['status']:<4} {'—':>6} {'—':>7} {'缺气象/训练跳过':>7}")
            continue
        peak = full_p(d)
        loo_p = loo.get(d)
        ok = "✓" if peak is not None and peak >= 55 else "✗"
        note = (lb.get("notes") or "")[:20]
        fog_h = feat(d, "hour_count_fog")
        dry = feat(d, "hour_count_dry_low_vis")
        peak_s = f"{peak:6.0f}" if peak is not None else "     —"
        loo_s = f"{loo_p*100:6.0f}" if loo_p is not None else "     —"
        print(
            f"{ok} {d:<10} {lb['status']:<4} "
            f"{peak_s} {loo_s} "
            f"{met['vis_min_m']:7.0f} {met['rh_mean']:4.0f} {met['rh850_mean']:5.0f} "
            f"{fog_h:5.0f} {dry:6.0f} {note}"
        )

    print("\n=== 2025-05 库内全部标注（含 none）===")
    for r in conn.execute(
        """
        SELECT date, status, notes FROM cloudsea_labels
        WHERE spot_id='wunvshan' AND viewpoint_id='dianjiangtai' AND date LIKE '2025-05%'
        ORDER BY date
        """
    ):
        print(r["date"], r["status"], (r["notes"] or "")[:40])

    conn.close()


if __name__ == "__main__":
    main()
