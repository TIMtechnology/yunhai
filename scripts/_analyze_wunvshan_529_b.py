#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from train_cloudsea_model import load_dataset  # noqa: E402


def g(X, fn, i, k):
    return X[i][fn.index(k)]


def main():
    db = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/data/cloudsea/cloudsea.db")
    X, y, meta, fn = load_dataset(
        db, spot_id="wunvshan", viewpoint_id="dianjiangtai", db_only=True
    )

    print("=== vis_min<500m 样本 ===")
    for i, m in enumerate(meta):
        v = g(X, fn, i, "vis_min")
        if v >= 500:
            continue
        print(
            m["date"],
            m["status"],
            f"vis={v:.0f}m",
            f"rh={g(X,fn,i,'rh_mean'):.0f}",
            f"rh850={g(X,fn,i,'rh850_mean'):.0f}",
            f"eff_low={g(X,fn,i,'effective_low_mean'):.0f}",
            f"fog_h={g(X,fn,i,'hour_count_fog'):.0f}",
            f"obs_depth={g(X,fn,i,'observable_depth_mean'):.0f}",
        )

    print("\n=== rh850<40 且 vis_min<2000 ===")
    for i, m in enumerate(meta):
        if g(X, fn, i, "rh850_mean") >= 40 or g(X, fn, i, "vis_min") >= 2000:
            continue
        print(
            m["date"],
            m["status"],
            f"vis={g(X,fn,i,'vis_min'):.0f}",
            f"rh850={g(X,fn,i,'rh850_mean'):.0f}",
            f"rh={g(X,fn,i,'rh_mean'):.0f}",
        )

    print("\n=== 5/01 vs 5/28 vs 5/29 ===")
    keys = [
        "vis_min",
        "vis_mean",
        "rh_mean",
        "rh850_mean",
        "effective_low_mean",
        "observable_depth_mean",
        "observable_fraction_max",
        "hour_count_fog",
        "cloud_mid_mean",
    ]
    for d in ["2026-05-01", "2026-05-28", "2026-05-29", "2026-05-20", "2026-05-22"]:
        i = next(j for j, m in enumerate(meta) if m["date"] == d)
        print(d, meta[i]["status"], {k: round(g(X, fn, i, k), 2) for k in keys})

    conn = sqlite3.connect(db)
    bulk = {
        r[0]
        for r in conn.execute(
            "SELECT date FROM cloudsea_labels WHERE spot_id='wunvshan' AND notes LIKE '%抖音%'"
        )
    }
    conn.close()
    keep = [i for i, m in enumerate(meta) if m["date"] not in bulk]
    X2, y2 = X[keep], y[keep]
    meta2 = [meta[i] for i in keep]
    idx = next(i for i, m in enumerate(meta2) if m["date"] == "2026-05-29")
    for tr, te in LeaveOneOut().split(X2):
        if te[0] != idx:
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
        m.fit(X2[tr], y2[tr])
        p = m.predict_proba(X2[te])[:, 1][0]
        print(f"\n排除 {len(bulk)} 条抖音补标后 n={len(y2)} LOOCV P(5/29)={p*100:.1f}%")


if __name__ == "__main__":
    main()
