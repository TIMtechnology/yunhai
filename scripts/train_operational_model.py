#!/usr/bin/env python3
"""Train and review a safe point-specific model from exact archived model runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _base in (ROOT / "backend", ROOT):
    if (_base / "app").is_dir():
        sys.path.insert(0, str(_base))
        break

from app.engine.cloudsea_features import filter_dawn_rows  # noqa: E402
from app.engine.cloudsea_scorer import score_cloudsea  # noqa: E402
from app.engine.cloudsea_policy import merge_cloudsea_policy_hour  # noqa: E402
from app.engine.ml_eligibility import spot_model_path  # noqa: E402
from app.engine.operational_ml import chronological_holdout_indices  # noqa: E402
from app.engine.utils import grade_from_probability  # noqa: E402
from app.engine.viewing_mode import resolve_viewing_mode  # noqa: E402
from app.models.schemas import FactorDetail, PredictionScore  # noqa: E402
from app.adapters.dem import get_terrain_context_sync  # noqa: E402
from app.engine.coord_transform import weather_coords  # noqa: E402
from app.services.cloudsea_store import (  # noqa: E402
    default_forecast_issue_time,
    load_forecast_archive_precursor,
    load_spatial_forecast_archive,
)
from app.adapters.open_meteo import operational_single_run_source  # noqa: E402
from app.services.meteo_backfill import precursor_hour_keys  # noqa: E402
from app.services.spot_loader import get_spot, get_viewpoint  # noqa: E402
from scripts.train_cloudsea_model import load_dataset  # noqa: E402

ARCHIVE_SOURCE_PREFIX = "open_meteo_single_runs:ecmwf_ifs:run="
MODEL_C = 0.1
HOLDOUT_DAYS = 15
MIN_HOLDOUT_POSITIVE = 3
MIN_HOLDOUT_NEGATIVE = 3
MIN_BALANCED_ACCURACY = 0.60
MIN_RECALL = 0.50
ML_BLEND_WEIGHT = 0.65
CALIBRATION_C = 1.0
CALIBRATION_SPLITS = 4

# Fixed before holdout evaluation. These are available in the HRES archive and
# in live forecasts; no pressure-level or terrain-constant fields are included.
OPERATIONAL_FEATURE_NAMES = [
    "cloud_low_mean",
    "cloud_mid_mean",
    "vis_min",
    "rh_mean",
    "wind_mean",
    "precip48",
    "evening_rh_mean",
    "evening_cloud_low_mean",
    "night_rh_mean",
    "night_cloud_low_mean",
    "delta_rh_night_to_dawn",
    "delta_cloud_low_evening_to_dawn",
    "doy_sin",
    "doy_cos",
]


def _make_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    C=MODEL_C,
                    max_iter=5000,
                    random_state=42,
                    solver="lbfgs",
                ),
            ),
        ]
    )


def _serialize_pipeline(model: Pipeline) -> dict[str, Any]:
    scaler = model.named_steps["scaler"]
    classifier = model.named_steps["clf"]
    return {
        "mean": [float(value) for value in scaler.mean_],
        "scale": [float(value) for value in scaler.scale_],
        "coef": [float(value) for value in classifier.coef_[0]],
        "intercept": float(classifier.intercept_[0]),
        "classes": [0, 1],
    }


def _metrics(
    y: np.ndarray,
    probabilities: np.ndarray,
    *,
    threshold: float = 0.5,
) -> dict[str, float]:
    predictions = (probabilities >= threshold).astype(int)
    result = {
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(y, predictions)),
        "balanced_accuracy": float(balanced_accuracy_score(y, predictions)),
        "precision": float(precision_score(y, predictions, zero_division=0)),
        "recall": float(recall_score(y, predictions, zero_division=0)),
        "f1": float(f1_score(y, predictions, zero_division=0)),
        "brier": float(brier_score_loss(y, probabilities)),
    }
    result["roc_auc"] = (
        float(roc_auc_score(y, probabilities)) if len(set(y.tolist())) == 2 else 0.0
    )
    return result


def _time_ordered_oof_logits(
    X: np.ndarray,
    y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    logits: list[float] = []
    labels: list[int] = []
    indexes: list[int] = []
    for train_idx, test_idx in TimeSeriesSplit(n_splits=CALIBRATION_SPLITS).split(X):
        if len(set(y[train_idx].tolist())) < 2:
            continue
        model = _make_pipeline()
        model.fit(X[train_idx], y[train_idx])
        logits.extend(float(value) for value in model.decision_function(X[test_idx]))
        labels.extend(int(value) for value in y[test_idx])
        indexes.extend(int(value) for value in test_idx)
    if len(logits) < 20 or len(set(labels)) < 2:
        raise ValueError("not enough time-ordered out-of-fold predictions for calibration")
    return (
        np.asarray(logits, dtype=float),
        np.asarray(labels, dtype=int),
        np.asarray(indexes, dtype=int),
    )


def _fit_platt_calibrator(
    logits: np.ndarray,
    y: np.ndarray,
) -> LogisticRegression:
    calibrator = LogisticRegression(
        C=CALIBRATION_C,
        max_iter=5000,
        random_state=42,
        solver="lbfgs",
    )
    calibrator.fit(logits.reshape(-1, 1), y)
    return calibrator


def _tune_threshold(
    y: np.ndarray,
    probabilities: np.ndarray,
) -> float:
    best: tuple[tuple[float, float, float], float] | None = None
    for threshold in np.arange(0.15, 0.61, 0.01):
        metrics = _metrics(y, probabilities, threshold=float(threshold))
        if metrics["recall"] < MIN_RECALL:
            continue
        score = (
            metrics["f1"],
            metrics["balanced_accuracy"],
            -abs(float(threshold) - 0.5),
        )
        if best is None or score > best[0]:
            best = (score, float(threshold))
    if best is None:
        raise ValueError("training-only threshold search could not meet recall floor")
    return best[1]


def _fit_calibrated_linear_model(
    X: np.ndarray,
    y: np.ndarray,
) -> tuple[dict[str, Any], Pipeline, LogisticRegression, np.ndarray, np.ndarray, np.ndarray]:
    oof_logits, oof_y, oof_idx = _time_ordered_oof_logits(X, y)
    calibrator = _fit_platt_calibrator(oof_logits, oof_y)
    base_model = _make_pipeline()
    base_model.fit(X, y)

    scaler = base_model.named_steps["scaler"]
    classifier = base_model.named_steps["clf"]
    calibration_slope = float(calibrator.coef_[0, 0])
    calibration_intercept = float(calibrator.intercept_[0])
    combined_coef = calibration_slope * classifier.coef_[0]
    combined_intercept = (
        calibration_slope * float(classifier.intercept_[0]) + calibration_intercept
    )
    serialized = {
        "mean": [float(value) for value in scaler.mean_],
        "scale": [float(value) for value in scaler.scale_],
        "coef": [float(value) for value in combined_coef],
        "intercept": float(combined_intercept),
        "classes": [0, 1],
    }
    return serialized, base_model, calibrator, oof_logits, oof_y, oof_idx


def _calibrated_probabilities(
    base_model: Pipeline,
    calibrator: LogisticRegression,
    X: np.ndarray,
) -> np.ndarray:
    logits = np.asarray(base_model.decision_function(X), dtype=float).reshape(-1, 1)
    return calibrator.predict_proba(logits)[:, 1]


def _production_day_probability(
    rows: list[dict[str, Any]],
    *,
    target_date: str,
    elevation: float,
    terrain: dict[str, Any],
    viewing_mode: str,
    cloudsea_months: list[int],
    ml_probability: float | None,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
) -> float:
    ml_score = None
    if ml_probability is not None:
        ml_percent = int(round(float(ml_probability) * 100))
        ml_score = PredictionScore(
            probability=ml_percent,
            grade=grade_from_probability(ml_percent),
            factors={
                "ml_model": FactorDetail(
                    score=ml_percent / 100.0,
                    weight=1.0,
                    label="ML 云海模型",
                    description="历史回放中的当日 ML 原始概率",
                    value=f"P_ml={ml_percent}%",
                    reference="operational_replay",
                )
            },
        )
    probabilities: list[float] = []
    for row in filter_dawn_rows(rows, target_date):
        hour = int(str(row["time"])[11:13])
        if not 3 <= hour < 7:
            continue
        rh = float(row.get("rh") or 70.0)
        cloud_low = float(row.get("cloud_low") or 0.0)
        cloud_mid = float(row.get("cloud_mid") or 0.0)
        cloud_high = float(row.get("cloud_high") or 0.0)
        wind = float(row.get("wind") or 3.0)
        precip_recent = float(row.get("precip48") or 0.0)
        temp = float(row.get("temp") or 15.0)
        dewpoint = float(row.get("dewpoint") or 10.0)
        visibility = (
            float(row["visibility"]) if row.get("visibility") is not None else None
        )
        timestamp = str(row.get("time") or "")
        sector_meteo = (terrain.get("sector_meteo_by_time") or {}).get(timestamp)
        rule_score = score_cloudsea(
            rh=rh,
            cloud_low=cloud_low,
            cloud_mid=cloud_mid,
            cloud_high=cloud_high,
            wind=wind,
            precip=float(row.get("precipitation") or 0.0),
            precip_recent=precip_recent,
            temp=temp,
            dewpoint=dewpoint,
            elevation=elevation,
            month=int(target_date[5:7]),
            cloudsea_months=cloudsea_months,
            visibility=visibility,
            viewing_mode=viewing_mode,
            terrain=terrain,
            sector_meteo=sector_meteo,
            summit_cloud_low=cloud_low,
            summit_rh=rh,
            retrospective=False,
        )
        policy = merge_cloudsea_policy_hour(
            rule_score,
            ml_score=ml_score,
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
            elevation=elevation,
            viewing_mode=viewing_mode,
            terrain=terrain,
            rh=rh,
            cloud_low=cloud_low,
            cloud_mid=cloud_mid,
            cloud_high=cloud_high,
            wind=wind,
            precip_recent=precip_recent,
            temp=temp,
            dewpoint=dewpoint,
            visibility=visibility,
            sector_meteo=sector_meteo,
            retrospective=False,
        )
        probabilities.append(policy.score.probability / 100.0)
    if not probabilities:
        raise ValueError(f"{target_date} has no 03:00–06:00 rule rows")
    return max(probabilities)


def _rule_probability(
    rows: list[dict[str, Any]],
    *,
    target_date: str,
    elevation: float,
    terrain: dict[str, Any],
    viewing_mode: str,
    cloudsea_months: list[int],
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
) -> float:
    return _production_day_probability(
        rows,
        target_date=target_date,
        elevation=elevation,
        terrain=terrain,
        viewing_mode=viewing_mode,
        cloudsea_months=cloudsea_months,
        ml_probability=None,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
    )


def _exact_archive_dates(
    db_path: Path,
    spot_id: str,
    viewpoint_id: str,
) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT target_date, issue_time, source, ts
            FROM meteo_forecast_archive
            WHERE spot_id=? AND viewpoint_id=?
            """,
            (spot_id, viewpoint_id),
        ).fetchall()
    exact_times_by_date: dict[str, set[str]] = {}
    for target_date, issue_time, source, timestamp in rows:
        target = str(target_date)
        expected_source = (
            ARCHIVE_SOURCE_PREFIX
            + f"{(date.fromisoformat(target) - timedelta(days=1)).isoformat()}T00:00"
        )
        if (
            str(issue_time) == default_forecast_issue_time(target)
            and str(source) == expected_source
        ):
            exact_times_by_date.setdefault(target, set()).add(str(timestamp))
    return {
        target
        for target, timestamps in exact_times_by_date.items()
        if set(precursor_hour_keys(target)).issubset(timestamps)
    }


def _dataset_fingerprint(
    dates: list[str],
    y: np.ndarray,
    X: np.ndarray,
    feature_names: list[str],
) -> str:
    payload = {
        "dates": dates,
        "labels": [int(value) for value in y],
        "feature_names": feature_names,
        "features": [[round(float(value), 8) for value in row] for row in X],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def train_operational_artifact(
    db_path: Path,
    *,
    spot_id: str,
    viewpoint_id: str,
    holdout_days: int = HOLDOUT_DAYS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    X_all, y, meta, all_names = load_dataset(
        db_path,
        approved_only=True,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        exclude_rain=False,
        use_terrain=False,
        use_observable_field=False,
        db_only=True,
        window="v7",
        mode="operational",
    )
    if len(meta) < 30:
        raise ValueError(f"exact operational archive has only {len(meta)} usable days")
    exact_dates = _exact_archive_dates(db_path, spot_id, viewpoint_id)
    unexpected = [item["date"] for item in meta if item["date"] not in exact_dates]
    if unexpected:
        raise ValueError(f"non-single-run archive rows found: {', '.join(unexpected)}")
    dates = [str(item["date"]) for item in meta]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("training dates must be unique and chronological")

    name_to_index = {name: idx for idx, name in enumerate(all_names)}
    missing = [name for name in OPERATIONAL_FEATURE_NAMES if name not in name_to_index]
    if missing:
        raise ValueError(f"feature implementation missing: {', '.join(missing)}")
    selected_idx = [name_to_index[name] for name in OPERATIONAL_FEATURE_NAMES]
    X = X_all[:, selected_idx]
    elevation = float(meta[0].get("elevation") or 804.0)
    viewpoint = get_viewpoint(spot_id, viewpoint_id)
    if viewpoint is None:
        raise ValueError(f"curated viewpoint not found: {spot_id}/{viewpoint_id}")
    spot = get_spot(spot_id)
    terrain_lat, terrain_lng = weather_coords(
        float(viewpoint.lat),
        float(viewpoint.lng),
        spot.coord_sys if spot is not None else "WGS84",
    )
    terrain = get_terrain_context_sync(
        terrain_lat,
        terrain_lng,
        elevation=elevation,
        profile_date=date.fromisoformat(dates[-1]),
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
    )
    viewing_mode, _, _ = resolve_viewing_mode(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        elevation=elevation,
        terrain=terrain,
        location_id=None,
    )
    cloudsea_months = (
        list((spot.seasonality or {}).get("cloudsea_months") or [])
        if spot is not None
        else []
    )
    spatial_enabled = bool(
        spot and (spot.rules or {}).get("spatial_valley_meteo")
    )
    spatial_by_date: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if spatial_enabled:
        for target in dates:
            index = load_spatial_forecast_archive(
                spot_id,
                viewpoint_id,
                target,
                expected_source=operational_single_run_source(date.fromisoformat(target)),
                db_path=db_path,
            )
            expected_dawn = {
                f"{target}T{hour:02d}:00"
                for hour in range(3, 7)
            }
            if not expected_dawn.issubset(index):
                raise ValueError(f"exact spatial archive incomplete for {target}")
            spatial_by_date[target] = index

    def policy_terrain(target: str) -> dict[str, Any]:
        if not spatial_enabled:
            return terrain
        return {
            **terrain,
            "sector_meteo_by_time": spatial_by_date[target],
            "sector_meteo_source": "ecmwf_ifs_d_minus_1_00z",
        }

    train_idx, holdout_idx = chronological_holdout_indices(
        dates,
        holdout_days=holdout_days,
    )
    y_train = y[train_idx].astype(int)
    y_holdout = y[holdout_idx].astype(int)
    positive_holdout = int(y_holdout.sum())
    negative_holdout = int(len(y_holdout) - positive_holdout)
    if positive_holdout < MIN_HOLDOUT_POSITIVE or negative_holdout < MIN_HOLDOUT_NEGATIVE:
        raise ValueError("chronological holdout does not contain enough examples of both classes")

    review_model = _make_pipeline()
    review_model.fit(X[train_idx], y_train)
    ml_prob = review_model.predict_proba(X[holdout_idx])[:, 1]
    review_threshold = 0.5
    rule_prob = np.asarray(
        [
            _rule_probability(
                load_forecast_archive_precursor(
                    spot_id,
                    viewpoint_id,
                    dates[idx],
                    db_path=db_path,
                ),
                target_date=dates[idx],
                elevation=elevation,
                terrain=policy_terrain(dates[idx]),
                viewing_mode=viewing_mode,
                cloudsea_months=cloudsea_months,
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
            )
            for idx in holdout_idx
        ],
        dtype=float,
    )
    blend_prob = np.asarray(
        [
            _production_day_probability(
                load_forecast_archive_precursor(
                    spot_id,
                    viewpoint_id,
                    dates[idx],
                    db_path=db_path,
                ),
                target_date=dates[idx],
                elevation=elevation,
                terrain=policy_terrain(dates[idx]),
                viewing_mode=viewing_mode,
                cloudsea_months=cloudsea_months,
                ml_probability=float(probability),
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
            )
            for idx, probability in zip(holdout_idx, ml_prob, strict=True)
        ],
        dtype=float,
    )
    no_spatial_blend_prob = np.asarray(
        [
            _production_day_probability(
                load_forecast_archive_precursor(
                    spot_id,
                    viewpoint_id,
                    dates[idx],
                    db_path=db_path,
                ),
                target_date=dates[idx],
                elevation=elevation,
                terrain=terrain,
                viewing_mode=viewing_mode,
                cloudsea_months=cloudsea_months,
                ml_probability=float(probability),
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
            )
            for idx, probability in zip(holdout_idx, ml_prob, strict=True)
        ],
        dtype=float,
    )
    raw_metrics = _metrics(y_holdout, ml_prob, threshold=review_threshold)
    rule_metrics = _metrics(y_holdout, rule_prob)
    blend_metrics = _metrics(y_holdout, blend_prob, threshold=review_threshold)
    no_spatial_blend_metrics = _metrics(
        y_holdout,
        no_spatial_blend_prob,
        threshold=review_threshold,
    )

    previous_window: dict[str, Any] | None = None
    rolling_stability = True
    if len(train_idx) >= holdout_days + 30:
        previous_holdout_idx = train_idx[-holdout_days:]
        previous_train_idx = train_idx[:-holdout_days]
        previous_model = _make_pipeline()
        previous_model.fit(X[previous_train_idx], y[previous_train_idx].astype(int))
        previous_ml_prob = previous_model.predict_proba(X[previous_holdout_idx])[:, 1]
        previous_blend_prob = np.asarray(
            [
                _production_day_probability(
                    load_forecast_archive_precursor(
                        spot_id,
                        viewpoint_id,
                        dates[idx],
                        db_path=db_path,
                    ),
                    target_date=dates[idx],
                    elevation=elevation,
                    terrain=policy_terrain(dates[idx]),
                    viewing_mode=viewing_mode,
                    cloudsea_months=cloudsea_months,
                    ml_probability=float(probability),
                    spot_id=spot_id,
                    viewpoint_id=viewpoint_id,
                )
                for idx, probability in zip(
                    previous_holdout_idx,
                    previous_ml_prob,
                    strict=True,
                )
            ],
            dtype=float,
        )
        previous_y = y[previous_holdout_idx].astype(int)
        previous_metrics = _metrics(previous_y, previous_blend_prob, threshold=0.5)
        rolling_stability = (
            previous_metrics["balanced_accuracy"] >= MIN_BALANCED_ACCURACY
            and previous_metrics["recall"] >= MIN_RECALL
        )
        previous_window = {
            "train_start": dates[previous_train_idx[0]],
            "train_end": dates[previous_train_idx[-1]],
            "holdout_start": dates[previous_holdout_idx[0]],
            "holdout_end": dates[previous_holdout_idx[-1]],
            "positive_days": int(previous_y.sum()),
            "negative_days": int(len(previous_y) - previous_y.sum()),
            "metrics": previous_metrics,
        }

    train_prevalence = float(np.mean(y_train))
    majority_prob = np.full(len(y_holdout), train_prevalence, dtype=float)
    majority_metrics = _metrics(y_holdout, majority_prob)
    checks = {
        "time_ordered": dates[train_idx[-1]] < dates[holdout_idx[0]],
        "both_classes": (
            positive_holdout >= MIN_HOLDOUT_POSITIVE
            and negative_holdout >= MIN_HOLDOUT_NEGATIVE
        ),
        "beats_majority": (
            blend_metrics["balanced_accuracy"] >= MIN_BALANCED_ACCURACY
            and blend_metrics["brier"] <= majority_metrics["brier"]
            and blend_metrics["recall"] >= MIN_RECALL
        ),
        "beats_rule": (
            blend_metrics["balanced_accuracy"] >= rule_metrics["balanced_accuracy"]
            and blend_metrics["f1"] >= rule_metrics["f1"]
            and blend_metrics["brier"] <= rule_metrics["brier"]
        ),
        "rolling_stability": rolling_stability,
    }
    quality_passed = all(checks.values())

    production_model = _make_pipeline()
    production_model.fit(X, y.astype(int))
    serialized_model = _serialize_pipeline(production_model)
    production_threshold = 0.5
    fingerprint = _dataset_fingerprint(dates, y, X, OPERATIONAL_FEATURE_NAMES)
    spatial_fingerprint = (
        hashlib.sha256(
            json.dumps(
                spatial_by_date,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if spatial_enabled
        else None
    )
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "version": "cloudsea_logreg_operational_v2",
        "algorithm": "logistic_regression",
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "window": "v7",
        "meteo_mode": "operational_single_run",
        "feature_names": list(OPERATIONAL_FEATURE_NAMES),
        "model": serialized_model,
        "decision_threshold": production_threshold,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_days": len(y),
        "training": {
            "date_start": dates[0],
            "date_end": dates[-1],
            "positive_days": int(y.sum()),
            "negative_days": int(len(y) - y.sum()),
            "data_sha256": fingerprint,
            "source": "open_meteo_single_runs",
            "model": "ecmwf_ifs",
            "run_hour_utc": 0,
            "fixed_c": MODEL_C,
            "calibration": "none_ui_threshold_0_50",
            "feature_policy": "predeclared_surface_v1",
            "spatial_policy": (
                "exact_valley_cells_v1" if spatial_enabled else "disabled"
            ),
            "spatial_data_sha256": spatial_fingerprint,
        },
        "evaluation": {
            "protocol": "chronological_holdout",
            "train_start": dates[train_idx[0]],
            "train_end": dates[train_idx[-1]],
            "holdout_start": dates[holdout_idx[0]],
            "holdout_end": dates[holdout_idx[-1]],
            "holdout_days": len(holdout_idx),
            "positive_days": positive_holdout,
            "negative_days": negative_holdout,
            "metrics": blend_metrics,
            "raw_ml_metrics": raw_metrics,
            "ablation": {
                "without_spatial_policy": no_spatial_blend_metrics,
                "balanced_accuracy_gain": (
                    blend_metrics["balanced_accuracy"]
                    - no_spatial_blend_metrics["balanced_accuracy"]
                ),
                "recall_gain": (
                    blend_metrics["recall"] - no_spatial_blend_metrics["recall"]
                ),
                "brier_improvement": (
                    no_spatial_blend_metrics["brier"] - blend_metrics["brier"]
                ),
            },
            "previous_chronological_window": previous_window,
            "baselines": {
                "majority_balanced_accuracy": majority_metrics["balanced_accuracy"],
                "majority_brier": majority_metrics["brier"],
                "rule_balanced_accuracy": rule_metrics["balanced_accuracy"],
                "rule_f1": rule_metrics["f1"],
                "rule_brier": rule_metrics["brier"],
            },
            "rule_context": {
                "meteorology": "same_exact_single_run_surface_inputs",
                "viewing_mode": viewing_mode,
                "terrain_source": str(terrain.get("source") or "unknown"),
                "elev_max_5km_m": float(terrain.get("elev_max_5km_m") or elevation),
                "local_water": bool(terrain.get("local_water")),
                "spatial_meteorology": (
                    "same_exact_single_run_valley_cells"
                    if spatial_enabled
                    else "disabled"
                ),
            },
            "production_blend_weight": ML_BLEND_WEIGHT,
            "production_policy": "canonical_hourly_fusion_v1",
            "review_threshold": review_threshold,
            "quality_gate": {
                "passed": quality_passed,
                "checks": checks,
                "thresholds": {
                    "min_balanced_accuracy": MIN_BALANCED_ACCURACY,
                    "min_recall": MIN_RECALL,
                    "min_positive_days": MIN_HOLDOUT_POSITIVE,
                    "min_negative_days": MIN_HOLDOUT_NEGATIVE,
                },
            },
        },
    }
    details = {
        "dates": dates,
        "holdout_dates": [dates[idx] for idx in holdout_idx],
        "holdout_labels": [int(value) for value in y_holdout],
        "raw_ml_probabilities": [float(value) for value in ml_prob],
        "rule_probabilities": [float(value) for value in rule_prob],
        "blend_probabilities": [float(value) for value in blend_prob],
        "no_spatial_blend_probabilities": [
            float(value) for value in no_spatial_blend_prob
        ],
        "spatial_changed_holdout_dates": [
            dates[idx]
            for idx, base_value, spatial_value in zip(
                holdout_idx,
                no_spatial_blend_prob,
                blend_prob,
                strict=True,
            )
            if abs(float(spatial_value) - float(base_value)) >= 0.01
        ],
        "previous_chronological_window": previous_window,
        "raw_ml_metrics": raw_metrics,
        "rule_metrics": rule_metrics,
        "blend_metrics": blend_metrics,
        "majority_metrics": majority_metrics,
        "quality_checks": checks,
    }
    return artifact, details


def main() -> None:
    parser = argparse.ArgumentParser(description="Train reviewed operational cloud-sea model")
    parser.add_argument("--db", required=True)
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    parser.add_argument("--output")
    parser.add_argument("--report")
    parser.add_argument("--allow-failed-gate", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    artifact, details = train_operational_artifact(
        db_path,
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
        holdout_days=args.holdout_days,
    )
    print(json.dumps(details, ensure_ascii=False, indent=2))
    passed = bool(artifact["evaluation"]["quality_gate"]["passed"])
    print(f"quality_gate={'PASS' if passed else 'FAIL'}")
    if not passed and not args.allow_failed_gate:
        raise SystemExit("quality gate failed; model artifact was not written")

    output = (
        Path(args.output)
        if args.output
        else spot_model_path(args.spot_id, args.viewpoint_id)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"model={output}")
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(details, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"report={report}")


if __name__ == "__main__":
    main()
