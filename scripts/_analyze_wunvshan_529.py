#!/usr/bin/env python3
"""分析五女山 5/29 LOOCV 低分原因（读 DB 缓存，无 API）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.services.meteo_backfill import load_label_sunrise_meteo  # noqa: E402
from train_cloudsea_model import load_dataset  # noqa: E402


def main() -> None:
    db = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/data/cloudsea/cloudsea.db")
    X, y, meta, fn = load_dataset(
        db, spot_id="wunvshan", viewpoint_id="dianjiangtai", db_only=True
    )
    idx29 = next(i for i, m in enumerate(meta) if m["date"] == "2026-05-29")

    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    x29s = Xs[idx29]
    dists = np.linalg.norm(Xs - x29s, axis=1)
    order = np.argsort(dists)

    conn = sqlite3.connect(db)
    print("=== 样本", len(y), "有", int(y.sum()), "无", int(len(y) - y.sum()))
    print("\n=== 与 5/29 特征最接近 15 天 ===")
    for rank, i in enumerate(order[:16]):
        m = meta[i]
        r = conn.execute(
            "SELECT notes FROM cloudsea_labels WHERE spot_id=? AND viewpoint_id=? AND date=?",
            ("wunvshan", "dianjiangtai", m["date"]),
        ).fetchone()
        note = (r[0] or "")[:28] if r else ""
        mk = " <<<" if m["date"] == "2026-05-29" else ""
        print(f"{rank+1:2}. {m['date']} dist={dists[i]:5.2f} {m['status']:6} {note}{mk}")

    for tr, te in LeaveOneOut().split(X):
        if te[0] != idx29:
            continue
        model = Pipeline(
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
        model.fit(X[tr], y[tr])
        p = model.predict_proba(X[te])[:, 1][0]
        print(f"\nLOOCV P(5/29) = {p * 100:.1f}%")
        xs = model.named_steps["scaler"].transform(X[te])[0]
        coef = model.named_steps["clf"].coef_[0]
        pairs = sorted(zip(fn, xs * coef, X[te][0]), key=lambda t: abs(t[1]), reverse=True)
        print("压分特征:")
        for name, c, raw in sorted(pairs, key=lambda t: t[1])[:8]:
            print(f"  {name}: raw={raw:.2f} contrib={c:+.3f}")
        print("抬分特征:")
        for name, c, raw in sorted(pairs, key=lambda t: t[1], reverse=True)[:8]:
            print(f"  {name}: raw={raw:.2f} contrib={c:+.3f}")

    keys = [
        "vis_min",
        "rh_mean",
        "rh850_mean",
        "inversion_mean",
        "cloud_mid_mean",
        "hour_count_fog",
        "observable_fraction_max",
        "effective_low_mean",
        "vis_limited_range_km_mean",
        "wind_mean",
    ]
    print("\n=== 5月关键日 ===")
    for d in [
        "2026-05-26",
        "2026-05-27",
        "2026-05-28",
        "2026-05-29",
        "2026-05-22",
        "2026-05-20",
        "2026-05-16",
        "2026-05-17",
        "2026-05-09",
    ]:
        i = next((j for j, m in enumerate(meta) if m["date"] == d), None)
        if i is None:
            continue
        vals = [X[i][fn.index(k)] for k in keys]
        print(d, meta[i]["status"], " | ".join(f"{k}={v:.1f}" for k, v in zip(keys, vals)))

    print("\n=== 逐时 5/28(none) vs 5/29(full) ===")
    rows28 = load_label_sunrise_meteo("wunvshan", "dianjiangtai", "2026-05-28", db_path=db)
    rows29 = load_label_sunrise_meteo("wunvshan", "dianjiangtai", "2026-05-29", db_path=db)
    for h in range(3, 7):
        r28 = next((r for r in rows28 if int(str(r["time"])[11:13]) == h), {})
        r29 = next((r for r in rows29 if int(str(r["time"])[11:13]) == h), {})
        print(
            f"T{h:02d}  528 vis={r28.get('visibility', 0)/1000:.1f}km rh={r28.get('rh')} rh850={r28.get('rh_850')} inv={r28.get('inversion')}  |  "
            f"529 vis={r29.get('visibility', 0)/1000:.1f}km rh={r29.get('rh')} rh850={r29.get('rh_850')} inv={r29.get('inversion')}"
        )

    # none within distance 10
    print("\n=== 距 5/29 <10 的 none 日（训练集里抢特征的）===")
    for i in order:
        if i == idx29:
            continue
        if dists[i] > 10:
            break
        if meta[i]["status"] != "none":
            continue
        r = conn.execute(
            "SELECT notes FROM cloudsea_labels WHERE spot_id=? AND viewpoint_id=? AND date=?",
            ("wunvshan", "dianjiangtai", meta[i]["date"]),
        ).fetchone()
        note = (r[0] or "")[:30] if r else ""
        print(f"  {meta[i]['date']} dist={dists[i]:.2f} {note}")

    conn.close()


if __name__ == "__main__":
    main()
