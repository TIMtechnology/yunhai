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
    DAY_FEATURE_NAMES_V2,
    DAY_FEATURE_NAMES_V3,
    MIST_DISCRIM_FEATURE_NAMES,
    OBSERVABLE_FEATURE_NAMES,
    PRECURSOR_FEATURE_NAMES,
    TERRAIN_FEATURE_NAMES,
    V7_FEATURE_NAMES,
    aggregate_day_features,
    aggregate_precursor_features,
    aggregate_v7_features,
    build_meteo_hour_row,
    filter_dawn_rows,
    label_to_target,
    meteo_row_complete,
)
from app.adapters.dem import get_terrain_context_sync  # noqa: E402
from app.engine.viewing_mode import resolve_viewing_mode  # noqa: E402
from app.engine.ml_eligibility import (  # noqa: E402
    min_labels_for_ml,
    spot_model_path,
    sunrise_window_rain_summary,
)
from app.services.meteo_backfill import (  # noqa: E402
    load_label_precursor_meteo,
    load_label_sunrise_meteo,
    precursor_window_meteo_complete,
    resolve_label_coords,
)
from app.services.cloudsea_store import (  # noqa: E402
    forecast_archive_precursor_complete,
    load_forecast_archive_precursor,
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
    *,
    db_path: Path,
    db_only: bool = False,
    window: str = "sunrise",
    mode: str = "oracle",
) -> list[dict]:
    spot_id = label["spot_id"]
    viewpoint_id = label["viewpoint_id"]
    day = label["date"]
    if window in ("precursor", "v7"):
        if mode == "operational":
            hour_rows = load_forecast_archive_precursor(
                spot_id, viewpoint_id, day, db_path=db_path
            )
        else:
            hour_rows = load_label_precursor_meteo(
                spot_id, viewpoint_id, day, db_path=db_path
            )
    else:
        hour_rows = load_label_sunrise_meteo(spot_id, viewpoint_id, day, db_path=db_path)
    if hour_rows and all(meteo_row_complete(r) for r in hour_rows):
        return hour_rows
    if db_only:
        return []
    lat, lng, _ = resolve_label_coords(label)
    return fetch_day_meteo(day, lat=lat, lng=lng)


def load_dataset(
    db_path: Path,
    *,
    approved_only: bool = False,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
    exclude_rain: bool = True,
    use_terrain: bool = True,
    use_observable_field: bool = True,
    db_only: bool = False,
    use_mist_discrim_features: bool = True,
    window: str = "sunrise",
    mode: str = "oracle",
) -> tuple[np.ndarray, np.ndarray, list[dict], list[str]]:
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

    X_rows: list[list[float]] = []
    y_rows: list[float] = []
    meta: list[dict] = []
    terrain_cache: dict[tuple[str, str], dict] = {}

    feature_names = (
        V7_FEATURE_NAMES
        if window == "v7"
        else PRECURSOR_FEATURE_NAMES
        if window == "precursor"
        else (
            DAY_FEATURE_NAMES
            if use_terrain and use_observable_field
            else DAY_FEATURE_NAMES_V3
            if use_terrain
            else DAY_FEATURE_NAMES_V2
        )
    )
    if window not in ("precursor", "v7") and not use_mist_discrim_features:
        feature_names = [n for n in feature_names if n not in MIST_DISCRIM_FEATURE_NAMES]

    use_terrain_feats = use_terrain and window != "precursor"

    for raw in labels:
        label = dict(raw)
        day = label["date"]
        lat, lng, elev = resolve_label_coords(label)
        hour_rows = load_meteo_rows(
            label,
            db_path=db_path,
            db_only=db_only,
            window=window,
            mode=mode,
        )
        if not hour_rows:
            reason = "DB 无缓存" if db_only else "no meteo"
            print(f"skip {day} {label['spot_id']}/{label['viewpoint_id']}: {reason}")
            continue
        if not all(meteo_row_complete(r) for r in hour_rows):
            print(f"skip {day} {label['spot_id']}/{label['viewpoint_id']}: incomplete meteo")
            continue
        dawn_rows = (
            hour_rows
            if window == "sunrise"
            else filter_dawn_rows(hour_rows, day)
            if window in ("precursor", "v7")
            else load_label_sunrise_meteo(
                label["spot_id"], label["viewpoint_id"], day, db_path=db_path
            )
        )
        if exclude_rain and dawn_rows and sunrise_window_rain_summary(dawn_rows)["has_rain"]:
            print(f"skip {day} {label['spot_id']}/{label['viewpoint_id']}: rain in sunrise window")
            continue

        terrain: dict | None = None
        if use_terrain_feats:
            tkey = (label["spot_id"], label["viewpoint_id"])
            if tkey not in terrain_cache:
                try:
                    from datetime import date as date_cls

                    profile_date = date_cls.fromisoformat(day)
                    terrain_cache[tkey] = get_terrain_context_sync(
                        lat,
                        lng,
                        elevation=elev,
                        profile_date=profile_date,
                        spot_id=label["spot_id"],
                        viewpoint_id=label["viewpoint_id"],
                    )
                except Exception as exc:
                    print(f"warn terrain {tkey}: {exc}")
                    terrain_cache[tkey] = {}
            terrain = dict(terrain_cache[tkey])
            viewing_mode, _, _ = resolve_viewing_mode(
                spot_id=label["spot_id"],
                viewpoint_id=label["viewpoint_id"],
                elevation=elev,
                terrain=terrain,
                location_id=label.get("location_id"),
            )
            terrain["viewing_mode"] = viewing_mode

        if window == "v7":
            day_feat = aggregate_v7_features(
                hour_rows,
                target_date=day,
                elevation=elev,
                terrain=terrain,
                use_observable_field=use_observable_field and use_terrain_feats,
                use_mist_discrim_features=use_mist_discrim_features,
            )
        elif window == "precursor":
            day_feat = aggregate_precursor_features(hour_rows, target_date=day, elevation=elev)
        else:
            day_feat = aggregate_day_features(
                hour_rows,
                elevation=elev,
                terrain=terrain,
                use_observable_field=use_observable_field and use_terrain_feats,
                use_mist_discrim_features=use_mist_discrim_features,
            )
        X_rows.append([day_feat[n] for n in feature_names])
        y_rows.append(label_to_target(label["status"]))
        meta.append(
            {
                "date": day,
                "status": label["status"],
                "lat": lat,
                "lng": lng,
                "spot_id": label["spot_id"],
                "viewpoint_id": label["viewpoint_id"],
                "viewing_mode": (terrain or {}).get("viewing_mode"),
            }
        )

    conn.close()
    return np.array(X_rows), np.array(y_rows), meta, feature_names


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
    feature_names: list[str],
    use_observable_field: bool,
    tuning: dict | None = None,
    window: str = "sunrise",
    meteo_mode: str = "oracle",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if window == "v7":
        version = "cloudsea_ml_v7_hybrid"
        aggregation = f"v6_dawn_plus_precursor_{meteo_mode}"
    elif window == "precursor":
        version = "cloudsea_ml_v7_precursor"
        aggregation = f"precursor_{meteo_mode}"
    elif tuning:
        version = "cloudsea_ml_v6_tuned"
        aggregation = "sunrise_window_03_07"
    else:
        version = (
            "cloudsea_ml_v5_dry_low_vis"
            if use_observable_field
            else "cloudsea_ml_v3_terrain"
        )
        aggregation = "sunrise_window_03_07"
    artifact = {
        "version": version,
        "algorithm": (
            "logistic_regression_v7_hybrid"
            if window == "v7"
            else "logistic_regression_precursor"
            if window == "precursor"
            else "logistic_regression_day"
        ),
        "feature_names": feature_names,
        "legacy_feature_names": DAY_FEATURE_NAMES_V2,
        "terrain_feature_names": TERRAIN_FEATURE_NAMES,
        "observable_feature_names": OBSERVABLE_FEATURE_NAMES if use_observable_field else [],
        "aggregation": aggregation,
        "window": window,
        "meteo_mode": meteo_mode,
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "model": model,
        "trained_at": datetime.utcnow().isoformat(),
        "n_days": len(y),
        "loocv_accuracy": float(accuracy_score(y, loo_pred)),
        "loocv_brier": float(brier_score_loss(y, loo_probs)),
        "excludes_rainy_sunrise_window": True,
    }
    if tuning:
        artifact.update(tuning)
    with open(path, "wb") as f:
        pickle.dump(artifact, f)


def print_loocv_details(
    y: np.ndarray,
    loo_probs: np.ndarray,
    loo_pred: np.ndarray,
    meta: list[dict],
) -> None:
    print("\n--- LOOCV 逐日 ---")
    print(f"{'日期':<12} {'标注':<8} {'P_loo':>6} {'pred':>5} {'ok':>4}")
    for i, m in enumerate(meta):
        actual = "有" if y[i] >= 0.5 else "无"
        pred = "有" if loo_pred[i] >= 0.5 else "无"
        ok = "✓" if loo_pred[i] == y[i] else "✗"
        print(f"{m['date']:<12} {m['status']:<8} {loo_probs[i]*100:5.0f}% {pred:>5} {ok:>4}")
    wrong = sum(1 for i in range(len(y)) if loo_pred[i] != y[i])
    print(f"错误 {wrong}/{len(y)}")


def train_group(
    db_path: Path,
    spot_id: str,
    viewpoint_id: str,
    *,
    approved_only: bool,
    models_dir: Path,
    default_output: Path | None = None,
    use_terrain: bool = True,
    use_observable_field: bool = True,
    db_only: bool = False,
    no_save: bool = False,
    loocv_detail: bool = False,
    enhanced: bool = False,
    min_recall: float | None = None,
    window: str = "sunrise",
    meteo_mode: str = "oracle",
) -> dict | None:
    min_n = min_labels_for_ml()
    X, y, meta, feature_names = load_dataset(
        db_path,
        approved_only=approved_only,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        exclude_rain=True,
        use_terrain=use_terrain,
        use_observable_field=use_observable_field,
        db_only=db_only,
        window=window,
        mode=meteo_mode,
    )
    pos = int(y.sum()) if len(y) else 0
    print(f"\n=== {spot_id}/{viewpoint_id} ===")
    print(f"窗口={window} 气象={meteo_mode}")
    print(f"有效标注日: {len(y)} | 有云海: {pos} | 无云海: {int(len(y) - pos)}")
    if len(y) < min_n:
        print(f"跳过：有效样本 {len(y)} < {min_n}")
        return None
    if len(set(y)) < 2:
        print("跳过：正负样本不足")
        return None

    tuning_extra: dict | None = None
    if enhanced:
        from app.engine.ml_tuning import apply_calibrator, build_production_pipeline, train_tuned

        tuned = train_tuned(
            X, y, meta, feature_names, min_recall=min_recall,
            feature_strategy=(
                "v7_l1" if window == "v7"
                else "precursor_core" if window == "precursor"
                else "none"
            ),
        )
        model = build_production_pipeline(X, y, tuned)
        loo_probs = tuned.loo_probs_calibrated
        loo_pred = (loo_probs >= tuned.decision_threshold).astype(int)
        loocv_acc = float(tuned.metrics_tuned["accuracy"])
        tuning_extra = {
            "selected_feature_names": tuned.feature_names,
            "decision_threshold": tuned.decision_threshold,
            "calibrator": tuned.calibrator,
            "tuning_c": tuned.c,
            "tuning_c_grid_scores": tuned.c_grid_scores,
            "monthly_cv": tuned.monthly_cv,
            "loocv_f1": tuned.metrics_tuned["f1"],
            "loocv_precision": tuned.metrics_tuned["precision"],
            "loocv_recall": tuned.metrics_tuned["recall"],
        }
        print(f"增强训练: C={tuned.c} 阈值={tuned.decision_threshold:.2f} 特征={len(tuned.feature_names)}")
        print(f"按月留一月 CV 准确率: {tuned.monthly_cv.get('overall_accuracy', 0):.3f}")
        X_fit = X[:, tuned.feature_mask]
        decision_thr = tuned.decision_threshold
    else:
        model, loo_probs, loo_pred = train_eval(X, y)
        loocv_acc = accuracy_score(y, loo_pred)
        X_fit = X
        decision_thr = 0.5

    prob = model.predict_proba(X_fit)[:, 1]
    if enhanced and tuning_extra and tuning_extra.get("calibrator"):
        prob = apply_calibrator(tuning_extra["calibrator"], prob)
    pred = (prob >= decision_thr).astype(int)
    try:
        auc = roc_auc_score(y, loo_probs) if len(set(y)) > 1 else 0.0
    except ValueError:
        auc = 0.0
    print(f"全量 accuracy: {accuracy_score(y, pred):.3f}")
    print(f"LOOCV accuracy: {loocv_acc:.3f}")
    print(f"LOOCV AUC: {auc:.3f}")
    print(f"LOOCV Brier: {brier_score_loss(y, loo_probs):.3f}")
    print(f"loocv_accuracy:{loocv_acc:.4f}")
    print(classification_report(y, pred, target_names=["无", "有"], zero_division=0))
    if loocv_detail:
        print_loocv_details(y, loo_probs, loo_pred, meta)

    if no_save:
        print("（--no-save：未写入模型文件）")
        return {
            "spot_id": spot_id,
            "viewpoint_id": viewpoint_id,
            "n_days": len(y),
            "loocv_accuracy": loocv_acc,
            "loocv_auc": auc,
            "output": None,
        }

    out = spot_model_path(spot_id, viewpoint_id, models_dir=models_dir)
    save_artifact(
        out,
        model=model,
        y=y,
        loo_probs=loo_probs,
        loo_pred=loo_pred,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        feature_names=feature_names,
        use_observable_field=use_observable_field,
        tuning=tuning_extra,
        window=window,
        meteo_mode=meteo_mode,
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
            feature_names=feature_names,
            use_observable_field=use_observable_field,
            tuning=tuning_extra,
            window=window,
            meteo_mode=meteo_mode,
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
    parser.add_argument("--no-terrain", action="store_true", help="训练时不加入 DEM 地形特征")
    parser.add_argument(
        "--use-observable-field",
        action="store_true",
        default=True,
        help="训练时加入可观测场特征（v4，默认开启；与 --no-terrain 互斥）",
    )
    parser.add_argument(
        "--no-observable-field",
        action="store_true",
        help="禁用可观测场特征，仅使用 v3 地形特征",
    )
    parser.add_argument(
        "--compare-terrain",
        action="store_true",
        help="对比 v2 特征 vs v3 地形特征的 LOOCV",
    )
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="仅使用 DB 缓存气象，缺失则跳过（不请求 Open-Meteo）",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="只做 LOOCV 评估，不写入 pkl",
    )
    parser.add_argument(
        "--loocv-detail",
        action="store_true",
        help="打印留一日交叉验证逐日明细",
    )
    parser.add_argument(
        "--enhanced",
        action="store_true",
        help="C 网格 + L1 特征 + 等渗校准 + LOOCV 阈值调优",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=None,
        help="增强模式下阈值调优的召回率下限",
    )
    parser.add_argument(
        "--window",
        choices=("sunrise", "precursor", "v7"),
        default="sunrise",
        help="特征时间窗：sunrise=03-07；precursor=仅过程26维；v7=v6 dawn+evening/night/趋势",
    )
    parser.add_argument(
        "--mode",
        dest="meteo_mode",
        choices=("oracle", "operational"),
        default="operational",
        help="precursor 训练气象源：oracle=事后真值，operational=预报 archive",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    models_dir = Path(args.output).resolve().parent

    use_terrain = not args.no_terrain
    use_observable = use_terrain and not args.no_observable_field

    if args.compare_terrain:
        X2, y2, _, _ = load_dataset(
            db_path, approved_only=args.approved_only, use_terrain=False, db_only=args.db_only
        )
        X3, y3, _, _ = load_dataset(
            db_path,
            approved_only=args.approved_only,
            use_terrain=True,
            use_observable_field=False,
            db_only=args.db_only,
        )
        X4, y4, _, _ = load_dataset(
            db_path,
            approved_only=args.approved_only,
            use_terrain=True,
            use_observable_field=True,
            db_only=args.db_only,
        )
        if len(y2) >= 5 and len(set(y2)) >= 2:
            _, _, pred2 = train_eval(X2, y2)
            print(f"LOOCV v2 (无地形): {accuracy_score(y2, pred2):.3f} n={len(y2)}")
        if len(y3) >= 5 and len(set(y3)) >= 2:
            _, _, pred3 = train_eval(X3, y3)
            print(f"LOOCV v3 (含地形): {accuracy_score(y3, pred3):.3f} n={len(y3)}")
        if len(y4) >= 5 and len(set(y4)) >= 2:
            _, _, pred4 = train_eval(X4, y4)
            print(f"LOOCV v4 (可观测场): {accuracy_score(y4, pred4):.3f} n={len(y4)}")
        return

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
            use_terrain=use_terrain,
            use_observable_field=use_observable,
            db_only=args.db_only,
            no_save=args.no_save,
            loocv_detail=args.loocv_detail,
            enhanced=args.enhanced,
            min_recall=args.min_recall,
            window=args.window,
            meteo_mode=args.meteo_mode,
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
