#!/usr/bin/env python3
"""从 cloudsea.db 标注数据训练云海 ML（按点位分模型；排除日出窗口降水日）。"""
from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
import urllib.request
from collections import defaultdict
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
from app.engine.ml_eligibility import (  # noqa: E402
    min_labels_for_ml,
    spot_model_path,
    sunrise_window_rain_summary,
)

LAT, LNG, ELEV = 41.31976, 125.40773, 804.0
WINDOW_START, WINDOW_END = 3, 7
WUNVSHAN_SPOT, WUNVSHAN_VP = "wunvshan", "dianjiangtai"


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


def _resolve_coords(label: dict, meteo_by_ts: dict[str, dict]) -> tuple[float, float, float]:
    from app.services.community_store import get_community_location
    from app.services.spot_loader import get_viewpoint

    lat, lng, elev = LAT, LNG, ELEV
    if label.get("lat") is not None and label.get("lng") is not None:
        lat, lng = float(label["lat"]), float(label["lng"])
        elev = float(label["elevation"]) if label.get("elevation") is not None else ELEV
    elif label["spot_id"] == "community" and label["viewpoint_id"]:
        loc = get_community_location(label["viewpoint_id"])
        if loc:
            lat, lng = float(loc["lat"]), float(loc["lng"])
            elev = float(loc["elevation"]) if loc.get("elevation") else ELEV
    elif label["spot_id"] and label["viewpoint_id"] and label["spot_id"] != "community":
        vp = get_viewpoint(label["spot_id"], label["viewpoint_id"])
        if vp:
            lat, lng = vp.lat, vp.lng
            elev = vp.elevation or ELEV
    return lat, lng, elev


def load_meteo_rows(
    label: dict,
    meteo_by_ts: dict[str, dict],
    *,
    lat: float,
    lng: float,
) -> list[dict]:
    day = label["date"]
    hour_rows = sorted(
        [v for k, v in meteo_by_ts.items() if k.startswith(f"{day}T")],
        key=lambda r: str(r.get("time")),
    )
    if not hour_rows or not all(meteo_row_complete(r) for r in hour_rows):
        hour_rows = fetch_day_meteo(day, lat=lat, lng=lng)
    return hour_rows


def load_dataset(
    db_path: Path,
    *,
    approved_only: bool = False,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
    exclude_rain: bool = True,
) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """加载标注日特征。排除未审核、降水日（默认）与气象缺失样本。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    clauses = ["1=1"]
    params: list[object] = []
    if approved_only:
        clauses.append("(review_status='approved' OR review_status IS NULL)")
    if spot_id:
        clauses.append("spot_id=?")
        params.append(spot_id)
    if viewpoint_id:
        clauses.append("viewpoint_id=?")
        params.append(viewpoint_id)
    labels = conn.execute(
        f"SELECT * FROM cloudsea_labels WHERE {' AND '.join(clauses)} ORDER BY date",
        params,
    ).fetchall()

    meteo_rows_db: list[sqlite3.Row] = conn.execute(
        "SELECT ts, raw_json FROM meteo_hourly"
    ).fetchall()
    meteo_by_ts: dict[str, dict] = {}
    for row in meteo_rows_db:
        meteo_by_ts[row["ts"]] = json.loads(row["raw_json"])

    X_rows: list[list[float]] = []
    y_rows: list[float] = []
    meta: list[dict] = []

    for raw in labels:
        label = dict(raw)
        day = label["date"]
        lat, lng, elev = _resolve_coords(label, meteo_by_ts)
        hour_rows = load_meteo_rows(label, meteo_by_ts, lat=lat, lng=lng)
        if not hour_rows:
            print(f"skip {day} {label['spot_id']}/{label['viewpoint_id']}: no meteo")
            continue
        if not all(meteo_row_complete(r) for r in hour_rows):
            print(f"skip {day} {label['spot_id']}/{label['viewpoint_id']}: incomplete meteo")
            continue
        if exclude_rain and sunrise_window_rain_summary(hour_rows)["has_rain"]:
            print(f"skip {day} {label['spot_id']}/{label['viewpoint_id']}: rain in sunrise window")
            continue
        day_feat = aggregate_day_features(hour_rows, elevation=elev)
        X_rows.append([day_feat[n] for n in DAY_FEATURE_NAMES])
        y_rows.append(label_to_target(label["status"]))
        meta.append(
            {
                "date": day,
                "status": label["status"],
                "lat": lat,
                "lng": lng,
                "spot_id": label["spot_id"],
                "viewpoint_id": label["viewpoint_id"],
            }
        )

    conn.close()
    return np.array(X_rows), np.array(y_rows), meta


def train_eval(X: np.ndarray, y: np.ndarray, *, c: float = 0.3) -> tuple[Pipeline, np.ndarray, np.ndarray]:
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(C=c, class_weight="balanced", max_iter=3000, random_state=42)),
        ]
    )
    model.fit(X, y)
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


def save_artifact(
    path: Path,
    *,
    model: Pipeline,
    y: np.ndarray,
    loo_probs: np.ndarray,
    loo_pred: np.ndarray,
    spot_id: str,
    viewpoint_id: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "version": "cloudsea_ml_v3_spot",
        "algorithm": "logistic_regression_day",
        "feature_names": DAY_FEATURE_NAMES,
        "aggregation": "sunrise_window_03_07",
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "model": model,
        "trained_at": datetime.utcnow().isoformat(),
        "n_days": len(y),
        "loocv_accuracy": float(accuracy_score(y, loo_pred)),
        "loocv_brier": float(brier_score_loss(y, loo_probs)),
        "excludes_rainy_sunrise_window": True,
    }
    with open(path, "wb") as f:
        pickle.dump(artifact, f)


def train_group(
    db_path: Path,
    spot_id: str,
    viewpoint_id: str,
    *,
    approved_only: bool,
    models_dir: Path,
    default_output: Path | None = None,
) -> dict | None:
    min_n = min_labels_for_ml()
    X, y, meta = load_dataset(
        db_path,
        approved_only=approved_only,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        exclude_rain=True,
    )
    pos = int(y.sum()) if len(y) else 0
    print(f"\n=== {spot_id}/{viewpoint_id} ===")
    print(f"有效标注日: {len(y)} | 有云海: {pos} | 无云海: {int(len(y) - pos)}")
    if len(y) < min_n:
        print(f"跳过：有效样本 {len(y)} < {min_n}")
        return None
    if len(set(y)) < 2:
        print("跳过：正负样本不足")
        return None

    model, loo_probs, loo_pred = train_eval(X, y)
    prob = model.predict_proba(X)[:, 1]
    pred = (prob >= 0.5).astype(int)
    loocv_acc = accuracy_score(y, loo_pred)
    print(f"全量 accuracy: {accuracy_score(y, pred):.3f}")
    print(f"LOOCV accuracy: {loocv_acc:.3f}")
    print(classification_report(y, pred, target_names=["无", "有"], zero_division=0))

    out = spot_model_path(spot_id, viewpoint_id, models_dir=models_dir)
    save_artifact(
        out,
        model=model,
        y=y,
        loo_probs=loo_probs,
        loo_pred=loo_pred,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
    )
    print(f"模型已保存: {out}")

    if default_output is not None:
        save_artifact(
            default_output,
            model=model,
            y=y,
            loo_probs=loo_probs,
            loo_pred=loo_pred,
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
        )
        print(f"默认模型已同步: {default_output}")

    return {
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "n_days": len(y),
        "loocv_accuracy": loocv_acc,
        "output": str(out),
    }


def list_label_groups(db_path: Path, *, approved_only: bool) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    clauses = ["1=1"]
    if approved_only:
        clauses.append("(review_status='approved' OR review_status IS NULL)")
    rows = conn.execute(
        f"""
        SELECT DISTINCT spot_id, viewpoint_id FROM cloudsea_labels
        WHERE {' AND '.join(clauses)}
        ORDER BY spot_id, viewpoint_id
        """
    ).fetchall()
    conn.close()
    return [(r["spot_id"], r["viewpoint_id"]) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "cloudsea" / "models" / "cloudsea_ml_v2.pkl"),
    )
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--spot-id")
    parser.add_argument("--viewpoint-id")
    args = parser.parse_args()

    db_path = Path(args.db)
    models_dir = Path(args.output).resolve().parent
    groups: list[tuple[str, str]]
    if args.spot_id and args.viewpoint_id:
        groups = [(args.spot_id, args.viewpoint_id)]
    else:
        groups = list_label_groups(db_path, approved_only=args.approved_only)

    trained: list[dict] = []
    for spot_id, viewpoint_id in groups:
        default_out = Path(args.output) if (spot_id, viewpoint_id) == (WUNVSHAN_SPOT, WUNVSHAN_VP) else None
        result = train_group(
            db_path,
            spot_id,
            viewpoint_id,
            approved_only=args.approved_only,
            models_dir=models_dir,
            default_output=default_out,
        )
        if result:
            trained.append(result)

    print(f"\n=== 训练汇总：成功 {len(trained)} 个点位 ===")
    for item in trained:
        print(
            f"  {item['spot_id']}/{item['viewpoint_id']}: "
            f"n={item['n_days']} LOOCV={item['loocv_accuracy']:.3f} -> {item['output']}"
        )


if __name__ == "__main__":
    main()
