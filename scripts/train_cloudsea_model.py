#!/usr/bin/env python3
"""从 cloudsea.db 标注数据训练云海 ML 模型 v2（含 700hPa / 逆温 / 高云特征）。"""
from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, classification_report, roc_auc_score
from sklearn.model_selection import LeaveOneOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
for _base in (ROOT / "backend", ROOT):
    if (_base / "app" / "adapters").exists():
        sys.path.insert(0, str(_base))
        break

from app.adapters.open_meteo import HOURLY_VARS  # noqa: E402
from app.engine.cloudsea_features import (  # noqa: E402
    DAY_FEATURE_NAMES,
    aggregate_day_features,
    build_meteo_hour_row,
    label_to_target,
    meteo_row_complete,
)

LAT, LNG, ELEV = 41.31976, 125.40773, 804.0
WINDOW_START, WINDOW_END = 3, 7


def fetch_day_meteo(day: str, *, lat: float = LAT, lng: float = LNG) -> list[dict]:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lng}&start_date={day}&end_date={day}"
        f"&hourly={','.join(HOURLY_VARS)}&timezone=Asia%2FShanghai"
    )
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.load(resp)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    rows: list[dict] = []
    for idx, t in enumerate(times):
        hour = int(t[11:13])
        if hour < WINDOW_START or hour >= WINDOW_END:
            continue
        rows.append(build_meteo_hour_row(hourly, idx))
    return rows


def load_dataset(db_path: Path, *, approved_only: bool = False) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if approved_only:
        labels = conn.execute(
            """
            SELECT * FROM cloudsea_labels
            WHERE review_status='approved' OR review_status IS NULL
            ORDER BY date
            """
        ).fetchall()
    else:
        labels = conn.execute("SELECT * FROM cloudsea_labels ORDER BY date").fetchall()

    from app.services.spot_loader import get_viewpoint

    meteo_rows: list[sqlite3.Row] = conn.execute(
        "SELECT ts, lat, lng, raw_json, spot_id, viewpoint_id FROM meteo_hourly"
    ).fetchall()
    meteo_by_ts: dict[str, dict] = {}
    for row in meteo_rows:
        meteo_by_ts[row["ts"]] = json.loads(row["raw_json"])

    X_rows: list[list[float]] = []
    y_rows: list[float] = []
    meta: list[dict] = []

    for label in labels:
        day = label["date"]
        target = label_to_target(label["status"])
        lat, lng, elev = LAT, LNG, ELEV
        if label.get("lat") is not None and label.get("lng") is not None:
            lat, lng = float(label["lat"]), float(label["lng"])
            elev = float(label["elevation"]) if label.get("elevation") is not None else ELEV
        elif label["spot_id"] == "community" and label["viewpoint_id"]:
            from app.services.community_store import get_community_location

            loc = get_community_location(label["viewpoint_id"])
            if loc:
                lat, lng = float(loc["lat"]), float(loc["lng"])
                elev = float(loc["elevation"]) if loc.get("elevation") else ELEV
        elif label["spot_id"] and label["viewpoint_id"] and label["spot_id"] != "community":
            vp = get_viewpoint(label["spot_id"], label["viewpoint_id"])
            if vp:
                lat, lng = vp.lat, vp.lng
                elev = vp.elevation or ELEV

        hour_rows = sorted(
            [v for k, v in meteo_by_ts.items() if k.startswith(f"{day}T")],
            key=lambda r: str(r.get("time")),
        )
        if not hour_rows or not all(meteo_row_complete(r) for r in hour_rows):
            hour_rows = fetch_day_meteo(day, lat=lat, lng=lng)
        if not hour_rows:
            print(f"skip {day}: no meteo")
            continue
        day_feat = aggregate_day_features(hour_rows, elevation=elev)
        X_rows.append([day_feat[n] for n in DAY_FEATURE_NAMES])
        y_rows.append(target)
        meta.append({"date": day, "status": label["status"], "lat": lat, "lng": lng})

    conn.close()
    return np.array(X_rows), np.array(y_rows), meta


def train_eval(X: np.ndarray, y: np.ndarray, meta: list[dict], *, c: float = 0.3) -> tuple[Pipeline, np.ndarray, np.ndarray]:
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(C=c, class_weight="balanced", max_iter=3000, random_state=42)),
        ]
    )
    model.fit(X, y)
    prob = model.predict_proba(X)[:, 1]
    pred = (prob >= 0.5).astype(int)

    loo = LeaveOneOut()
    loo_probs = np.zeros(len(y))
    for train_idx, test_idx in loo.split(X):
        m = Pipeline(
            [
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(C=c, class_weight="balanced", max_iter=3000, random_state=42)),
            ]
        )
        m.fit(X[train_idx], y[train_idx])
        loo_probs[test_idx[0]] = m.predict_proba(X[test_idx])[:, 1][0]
    loo_pred = (loo_probs >= 0.5).astype(int)
    return model, loo_probs, loo_pred


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "cloudsea" / "models" / "cloudsea_ml_v2.pkl"),
    )
    parser.add_argument("--approved-only", action="store_true")
    args = parser.parse_args()

    X, y, meta = load_dataset(Path(args.db), approved_only=args.approved_only)
    print(f"标注日: {len(y)} | 有云海: {int(y.sum())} | 无云海: {int(len(y) - y.sum())}")
    print(f"特征数: {len(DAY_FEATURE_NAMES)}")

    model, loo_probs, loo_pred = train_eval(X, y, meta)
    prob = model.predict_proba(X)[:, 1]
    pred = (prob >= 0.5).astype(int)

    print(f"\n=== 全量拟合 (n={len(y)}) ===")
    print(f"accuracy: {accuracy_score(y, pred):.3f}")
    print(f"brier: {brier_score_loss(y, prob):.3f}")
    print(f"auc: {roc_auc_score(y, prob):.3f}")
    print(classification_report(y, pred, target_names=["无", "有"], zero_division=0))

    print(f"\n=== 留一日交叉验证 LOOCV (n={len(y)}) ===")
    loocv_acc = accuracy_score(y, loo_pred)
    print(f"accuracy: {loocv_acc:.3f}")
    print(f"loocv_accuracy: {loocv_acc:.3f}")
    print(f"brier: {brier_score_loss(y, loo_probs):.3f}")
    if len(set(y)) > 1:
        print(f"auc: {roc_auc_score(y, loo_probs):.3f}")

    print("\n=== 逐日 LOOCV ===")
    for i, m in enumerate(meta):
        mark = "OK" if loo_pred[i] == y[i] else "X"
        print(f"  {mark} {m['date']} 标注={m['status']:7} ml={loo_probs[i]*100:5.1f}%")

    clf: LogisticRegression = model.named_steps["clf"]
    coefs = dict(zip(DAY_FEATURE_NAMES, clf.coef_[0]))
    print("\n=== 特征系数 (|coef| top 12) ===")
    for k, v in sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True)[:12]:
        print(f"  {k:20} {v:+.3f}")

    artifact = {
        "version": "cloudsea_ml_v2",
        "algorithm": "logistic_regression_day",
        "feature_names": DAY_FEATURE_NAMES,
        "aggregation": "sunrise_window_03_07",
        "model": model,
        "trained_at": datetime.utcnow().isoformat(),
        "n_days": len(y),
        "loocv_accuracy": float(accuracy_score(y, loo_pred)),
        "loocv_brier": float(brier_score_loss(y, loo_probs)),
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        pickle.dump(artifact, f)
    print(f"\n模型已保存: {out}")


if __name__ == "__main__":
    main()
