"""云海日尺度 ML：LOOCV 调参、阈值、L1 特征、概率校准、按月验证。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import LeaveOneOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


DEFAULT_C_GRID: tuple[float, ...] = (0.05, 0.1, 0.3, 1.0)

# 跨 sklearn 版本稳定的增强特征集（v5 判别 + 本地/线上 LOOCV 验证过的气象因子）
STABLE_ENHANCED_FEATURES: tuple[str, ...] = (
    "rh700_mean",
    "rh850_mean",
    "rh_mean",
    "wind_max",
    "precip48",
    "month",
    "inversion_mean",
    "inversion_max",
    "hour_count_type_a",
    "hour_count_type_b",
    "cloud_base_minus_valley_mean",
    "vis_min",
    "hour_count_dry_low_vis",
    "hour_count_dry_low_vis_boost",
    "hour_count_wet_low_vis",
    "day_dry_low_vis_flag",
    "hour_count_fog_boost",
    "hour_count_fog",
)


def _subset(X: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    if mask is None:
        return X
    return X[:, mask]


def _make_pipeline(
    *,
    c: float = 0.3,
    penalty: Literal["l1", "l2"] = "l2",
) -> Pipeline:
    kw: dict[str, Any] = {
        "C": c,
        "class_weight": "balanced",
        "max_iter": 5000,
        "random_state": 42,
        "solver": "saga" if penalty == "l1" else "lbfgs",
        "penalty": penalty,
    }
    return Pipeline([("scaler", StandardScaler()), ("clf", LogisticRegression(**kw))])


def loo_predict_proba(
    X: np.ndarray,
    y: np.ndarray,
    *,
    c: float = 0.3,
    penalty: Literal["l1", "l2"] = "l2",
    feature_mask: np.ndarray | None = None,
) -> np.ndarray:
    Xs = _subset(X, feature_mask)
    loo = LeaveOneOut()
    probs = np.zeros(len(y), dtype=float)
    for train_idx, test_idx in loo.split(Xs):
        pipe = _make_pipeline(c=c, penalty=penalty)
        pipe.fit(Xs[train_idx], y[train_idx])
        probs[test_idx[0]] = float(pipe.predict_proba(Xs[test_idx])[:, 1][0])
    return probs


def metrics_at_threshold(
    y: np.ndarray,
    probs: np.ndarray,
    threshold: float,
) -> dict[str, float]:
    pred = (probs >= threshold).astype(int)
    out: dict[str, float] = {
        "threshold": threshold,
        "accuracy": float(accuracy_score(y, pred)),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "brier": float(brier_score_loss(y, probs)),
    }
    try:
        out["roc_auc"] = float(roc_auc_score(y, probs))
    except ValueError:
        out["roc_auc"] = float("nan")
    return out


def tune_threshold(
    y: np.ndarray,
    probs: np.ndarray,
    *,
    objective: Literal["f1", "accuracy"] = "f1",
    min_recall: float | None = None,
    grid: np.ndarray | None = None,
) -> tuple[float, dict[str, float]]:
    if grid is None:
        grid = np.arange(0.15, 0.86, 0.01)
    best_t = 0.5
    best_score = -1.0
    best_m: dict[str, float] = {}
    for t in grid:
        m = metrics_at_threshold(y, probs, float(t))
        if min_recall is not None and m["recall"] < min_recall:
            continue
        score = m[objective]
        if score > best_score:
            best_score = score
            best_t = float(t)
            best_m = m
    if not best_m:
        best_m = metrics_at_threshold(y, probs, 0.5)
        best_t = 0.5
    return best_t, best_m


def select_c_by_loocv(
    X: np.ndarray,
    y: np.ndarray,
    c_grid: tuple[float, ...] = DEFAULT_C_GRID,
    *,
    penalty: Literal["l1", "l2"] = "l2",
    feature_mask: np.ndarray | None = None,
    metric: Literal["accuracy", "f1"] = "f1",
) -> tuple[float, dict[float, float]]:
    scores: dict[float, float] = {}
    for c in c_grid:
        probs = loo_predict_proba(X, y, c=c, penalty=penalty, feature_mask=feature_mask)
        _, m = tune_threshold(y, probs, objective=metric)
        scores[c] = m[metric]
    best_c = max(scores, key=scores.get)
    return best_c, scores


def l1_feature_mask(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    *,
    c: float = 0.1,
    min_nonzero: int = 8,
) -> tuple[np.ndarray, list[str]]:
    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    C=c,
                    penalty="l1",
                    solver="liblinear",
                    class_weight="balanced",
                    max_iter=5000,
                    random_state=42,
                ),
            ),
        ]
    )
    pipe.fit(X, y)
    coef = pipe.named_steps["clf"].coef_[0]
    mask = np.abs(coef) > 1e-6
    if mask.sum() < min_nonzero:
        order = np.argsort(np.abs(coef))[::-1][:min_nonzero]
        mask = np.zeros_like(mask, dtype=bool)
        mask[order] = True
    selected = [feature_names[i] for i in range(len(feature_names)) if mask[i]]
    return mask, selected


def fit_isotonic_calibrator(y: np.ndarray, probs: np.ndarray) -> IsotonicRegression:
    cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    cal.fit(probs, y)
    return cal


def apply_calibrator(cal: IsotonicRegression | None, probs: np.ndarray) -> np.ndarray:
    if cal is None:
        return probs
    flat = np.asarray(probs, dtype=float).reshape(-1)
    return np.clip(cal.predict(flat), 0.0, 1.0)


def monthly_leave_one_month_out(
    X: np.ndarray,
    y: np.ndarray,
    meta: list[dict],
    *,
    c: float,
    threshold: float,
    feature_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    months = sorted({m["date"][:7] for m in meta})
    by_month: dict[str, list[int]] = {mo: [] for mo in months}
    for i, m in enumerate(meta):
        by_month[m["date"][:7]].append(i)

    details: list[dict] = []
    all_true: list[int] = []
    all_pred: list[int] = []
    for hold_month in months:
        test_idx = by_month[hold_month]
        train_idx = [i for mo in months if mo != hold_month for i in by_month[mo]]
        if not train_idx or not test_idx or len(set(y[train_idx])) < 2:
            continue
        Xs = _subset(X, feature_mask)
        pipe = _make_pipeline(c=c)
        pipe.fit(Xs[train_idx], y[train_idx])
        probs = pipe.predict_proba(Xs[test_idx])[:, 1]
        pred = (probs >= threshold).astype(int)
        acc = float(accuracy_score(y[test_idx], pred))
        details.append(
            {
                "month": hold_month,
                "n": len(test_idx),
                "accuracy": acc,
                "dates": [meta[i]["date"] for i in test_idx],
            }
        )
        all_true.extend(y[test_idx].tolist())
        all_pred.extend(pred.tolist())

    overall = (
        float(accuracy_score(all_true, all_pred))
        if all_true
        else float("nan")
    )
    return {"overall_accuracy": overall, "months": details, "n_months": len(details)}


@dataclass
class TunedModelResult:
    c: float
    penalty: str
    feature_names: list[str]
    feature_mask: np.ndarray
    decision_threshold: float
    calibrator: IsotonicRegression | None
    loo_probs_raw: np.ndarray
    loo_probs_calibrated: np.ndarray
    metrics_default_05: dict[str, float] = field(default_factory=dict)
    metrics_tuned: dict[str, float] = field(default_factory=dict)
    c_grid_scores: dict[float, float] = field(default_factory=dict)
    monthly_cv: dict[str, Any] = field(default_factory=dict)
    feature_strategy: str = "core"


def core_feature_mask(feature_names: list[str]) -> tuple[np.ndarray, list[str]]:
    mask = np.array([n in STABLE_ENHANCED_FEATURES for n in feature_names], dtype=bool)
    selected = [feature_names[i] for i in range(len(feature_names)) if mask[i]]
    return mask, selected


def train_tuned(
    X: np.ndarray,
    y: np.ndarray,
    meta: list[dict],
    feature_names: list[str],
    *,
    c_grid: tuple[float, ...] = DEFAULT_C_GRID,
    feature_strategy: Literal["core", "l1", "none"] = "none",
    calibrate: bool = True,
    threshold_objective: Literal["f1", "accuracy"] = "f1",
    min_recall: float | None = None,
) -> TunedModelResult:
    best_c, c_scores = select_c_by_loocv(
        X, y, c_grid, penalty="l2", metric=threshold_objective
    )
    mask: np.ndarray | None = None
    active_names = list(feature_names)
    selection_note = "全特征"
    if feature_strategy == "core":
        mask, active_names = core_feature_mask(feature_names)
        selection_note = f"稳定核心特征 {len(active_names)} 维"
        best_c, c_scores = select_c_by_loocv(
            X, y, c_grid, penalty="l2", feature_mask=mask, metric=threshold_objective
        )
    elif feature_strategy == "l1" and X.shape[1] > 12:
        mask, active_names = l1_feature_mask(X, y, feature_names, c=min(0.1, best_c))
        selection_note = f"L1 筛选 {len(active_names)} 维"
        best_c, c_scores = select_c_by_loocv(
            X, y, c_grid, penalty="l2", feature_mask=mask, metric=threshold_objective
        )

    loo_raw = loo_predict_proba(X, y, c=best_c, feature_mask=mask)
    metrics_05 = metrics_at_threshold(y, loo_raw, 0.5)

    calibrator: IsotonicRegression | None = None
    loo_cal = loo_raw
    if calibrate and len(y) >= 15:
        calibrator = fit_isotonic_calibrator(y, loo_raw)
        loo_cal = apply_calibrator(calibrator, loo_raw)

    threshold, metrics_tuned = tune_threshold(
        y,
        loo_cal,
        objective=threshold_objective,
        min_recall=min_recall,
    )
    monthly = monthly_leave_one_month_out(
        X, y, meta, c=best_c, threshold=threshold, feature_mask=mask
    )

    result_mask = mask if mask is not None else np.ones(X.shape[1], dtype=bool)
    return TunedModelResult(
        c=best_c,
        penalty="l2",
        feature_names=active_names,
        feature_mask=result_mask,
        feature_strategy=selection_note,
        decision_threshold=threshold,
        calibrator=calibrator,
        loo_probs_raw=loo_raw,
        loo_probs_calibrated=loo_cal,
        metrics_default_05=metrics_05,
        metrics_tuned=metrics_tuned,
        c_grid_scores=c_scores,
        monthly_cv=monthly,
    )


def build_production_pipeline(
    X: np.ndarray,
    y: np.ndarray,
    result: TunedModelResult,
) -> Pipeline:
    Xs = _subset(X, result.feature_mask)
    pipe = _make_pipeline(c=result.c, penalty="l2")
    pipe.fit(Xs, y)
    return pipe


def predict_proba_day(
    pipeline: Pipeline,
    x_row: np.ndarray,
    *,
    feature_mask: np.ndarray | None = None,
    calibrator: IsotonicRegression | None = None,
) -> float:
    xs = _subset(x_row.reshape(1, -1), feature_mask)
    prob = float(pipeline.predict_proba(xs)[0, 1])
    if calibrator is not None:
        prob = float(apply_calibrator(calibrator, np.array([prob]))[0])
    return prob
