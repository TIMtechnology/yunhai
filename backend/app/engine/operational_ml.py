from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from app.engine.cloudsea_features import V7_FEATURE_NAMES

SAFE_ARTIFACT_SCHEMA_VERSION = 1
SAFE_ARTIFACT_ALGORITHM = "logistic_regression"
SAFE_ARTIFACT_SOURCE = "open_meteo_single_runs"
SAFE_ARTIFACT_PROTOCOL = "chronological_holdout"
MAX_ARTIFACT_BYTES = 1024 * 1024


class ArtifactValidationError(ValueError):
    """Raised when a model artifact is unsafe, malformed, or failed review."""


def chronological_holdout_indices(
    dates: Sequence[str],
    *,
    holdout_days: int,
) -> tuple[np.ndarray, np.ndarray]:
    if holdout_days <= 0 or holdout_days >= len(dates):
        raise ValueError("holdout_days must leave non-empty train and holdout sets")
    normalized = [str(value) for value in dates]
    if normalized != sorted(normalized) or len(set(normalized)) != len(normalized):
        raise ValueError("dates must be unique and sorted chronologically")
    split = len(normalized) - holdout_days
    train_idx = np.arange(0, split, dtype=int)
    holdout_idx = np.arange(split, len(normalized), dtype=int)
    if normalized[train_idx[-1]] >= normalized[holdout_idx[0]]:
        raise ValueError("training dates must be earlier than holdout dates")
    return train_idx, holdout_idx


def _reject_json_constant(value: str) -> None:
    raise ArtifactValidationError(f"non-finite JSON constant is forbidden: {value}")


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{name} must be an object")
    return value


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactValidationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ArtifactValidationError(f"{name} must be finite")
    return result


def _finite_vector(value: Any, name: str, *, length: int) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ArtifactValidationError(f"{name} must contain exactly {length} values")
    return [_finite_number(item, f"{name}[{idx}]") for idx, item in enumerate(value)]


def _iso_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ArtifactValidationError(f"{name} must be an ISO date") from exc


def validate_safe_artifact(
    artifact: Any,
    *,
    expected_spot_id: str | None = None,
    expected_viewpoint_id: str | None = None,
) -> dict[str, Any]:
    data = _require_mapping(artifact, "artifact")
    if data.get("schema_version") != SAFE_ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("unsupported model schema_version")
    if data.get("algorithm") != SAFE_ARTIFACT_ALGORITHM:
        raise ArtifactValidationError("unsupported model algorithm")
    version = data.get("version")
    if version not in {
        "cloudsea_logreg_operational_v1",
        "cloudsea_logreg_operational_v2",
    }:
        raise ArtifactValidationError("unsupported model version")
    try:
        trained_at = datetime.fromisoformat(str(data.get("trained_at") or ""))
    except ValueError as exc:
        raise ArtifactValidationError("trained_at must be an ISO datetime") from exc
    if trained_at.tzinfo is None:
        raise ArtifactValidationError("trained_at must include a timezone")

    spot_id = data.get("spot_id")
    viewpoint_id = data.get("viewpoint_id")
    if not isinstance(spot_id, str) or not spot_id:
        raise ArtifactValidationError("spot_id is required")
    if not isinstance(viewpoint_id, str) or not viewpoint_id:
        raise ArtifactValidationError("viewpoint_id is required")
    if expected_spot_id is not None and spot_id != expected_spot_id:
        raise ArtifactValidationError("model spot_id does not match request")
    if expected_viewpoint_id is not None and viewpoint_id != expected_viewpoint_id:
        raise ArtifactValidationError("model viewpoint_id does not match request")
    if data.get("window") != "v7":
        raise ArtifactValidationError("only the reviewed v7 operational window is supported")
    if data.get("meteo_mode") != "operational_single_run":
        raise ArtifactValidationError("model must use operational single-run weather")

    feature_names = data.get("feature_names")
    if (
        not isinstance(feature_names, list)
        or not feature_names
        or not all(isinstance(name, str) for name in feature_names)
        or len(set(feature_names)) != len(feature_names)
    ):
        raise ArtifactValidationError("feature_names must be a non-empty unique string list")
    unsupported = sorted(set(feature_names) - set(V7_FEATURE_NAMES))
    if unsupported:
        raise ArtifactValidationError(f"unsupported features: {', '.join(unsupported)}")

    model = _require_mapping(data.get("model"), "model")
    feature_count = len(feature_names)
    mean = _finite_vector(model.get("mean"), "model.mean", length=feature_count)
    scale = _finite_vector(model.get("scale"), "model.scale", length=feature_count)
    coef = _finite_vector(model.get("coef"), "model.coef", length=feature_count)
    if any(value <= 0 for value in scale):
        raise ArtifactValidationError("model.scale values must be positive")
    intercept = _finite_number(model.get("intercept"), "model.intercept")
    if model.get("classes") != [0, 1]:
        raise ArtifactValidationError("model.classes must be [0, 1]")

    threshold = _finite_number(data.get("decision_threshold"), "decision_threshold")
    if not 0.0 < threshold < 1.0:
        raise ArtifactValidationError("decision_threshold must be between zero and one")
    if version == "cloudsea_logreg_operational_v2" and not math.isclose(
        threshold,
        0.5,
        abs_tol=1e-12,
    ):
        raise ArtifactValidationError("v2 decision_threshold must match the UI 0.50 threshold")
    n_days = data.get("n_days")
    if isinstance(n_days, bool) or not isinstance(n_days, int) or n_days < 30:
        raise ArtifactValidationError("n_days must meet the 30-day production minimum")

    training = _require_mapping(data.get("training"), "training")
    if training.get("source") != SAFE_ARTIFACT_SOURCE:
        raise ArtifactValidationError("training source is not an exact single-run archive")
    if training.get("model") != "ecmwf_ifs":
        raise ArtifactValidationError("training model is not the reviewed ECMWF IFS HRES source")
    if training.get("run_hour_utc") != 0:
        raise ArtifactValidationError("training run must be 00Z to precede the decision cutoff")
    training_start = _iso_date(training.get("date_start"), "training.date_start")
    training_end = _iso_date(training.get("date_end"), "training.date_end")
    if training_start > training_end:
        raise ArtifactValidationError("training date range is reversed")
    positive_days = training.get("positive_days")
    negative_days = training.get("negative_days")
    if (
        isinstance(positive_days, bool)
        or not isinstance(positive_days, int)
        or positive_days <= 0
        or isinstance(negative_days, bool)
        or not isinstance(negative_days, int)
        or negative_days <= 0
        or positive_days + negative_days != n_days
    ):
        raise ArtifactValidationError("training class counts do not match n_days")
    fingerprint = training.get("data_sha256")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise ArtifactValidationError("training data_sha256 is invalid")
    if version == "cloudsea_logreg_operational_v2":
        if training.get("calibration") != "none_ui_threshold_0_50":
            raise ArtifactValidationError("v2 calibration policy is invalid")
        if training.get("spatial_policy") != "exact_valley_cells_v1":
            raise ArtifactValidationError("v2 spatial policy is invalid")
        spatial_fingerprint = training.get("spatial_data_sha256")
        if (
            not isinstance(spatial_fingerprint, str)
            or len(spatial_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in spatial_fingerprint)
        ):
            raise ArtifactValidationError("v2 spatial data fingerprint is invalid")

    evaluation = _require_mapping(data.get("evaluation"), "evaluation")
    if evaluation.get("protocol") != SAFE_ARTIFACT_PROTOCOL:
        raise ArtifactValidationError("model lacks chronological holdout evaluation")
    if version == "cloudsea_logreg_operational_v2":
        if evaluation.get("production_policy") != "canonical_hourly_fusion_v1":
            raise ArtifactValidationError("v2 production fusion policy is invalid")
        review_threshold = _finite_number(
            evaluation.get("review_threshold"),
            "evaluation.review_threshold",
        )
        if not math.isclose(review_threshold, 0.5, abs_tol=1e-12):
            raise ArtifactValidationError("v2 review threshold must be 0.50")
    holdout_positive = evaluation.get("positive_days")
    holdout_negative = evaluation.get("negative_days")
    if (
        isinstance(holdout_positive, bool)
        or not isinstance(holdout_positive, int)
        or holdout_positive <= 0
        or isinstance(holdout_negative, bool)
        or not isinstance(holdout_negative, int)
        or holdout_negative <= 0
    ):
        raise ArtifactValidationError("holdout must contain both classes")
    holdout_days = evaluation.get("holdout_days")
    if (
        isinstance(holdout_days, bool)
        or not isinstance(holdout_days, int)
        or holdout_days < 10
        or holdout_days != holdout_positive + holdout_negative
    ):
        raise ArtifactValidationError("holdout is too small for production review")
    review_train_start = _iso_date(evaluation.get("train_start"), "evaluation.train_start")
    review_train_end = _iso_date(evaluation.get("train_end"), "evaluation.train_end")
    holdout_start = _iso_date(evaluation.get("holdout_start"), "evaluation.holdout_start")
    holdout_end = _iso_date(evaluation.get("holdout_end"), "evaluation.holdout_end")
    if not (
        training_start <= review_train_start <= review_train_end
        < holdout_start <= holdout_end <= training_end
    ):
        raise ArtifactValidationError("evaluation dates are not a chronological holdout")
    metrics = _require_mapping(evaluation.get("metrics"), "evaluation.metrics")
    for key in ("balanced_accuracy", "recall", "f1", "brier"):
        value = _finite_number(metrics.get(key), f"evaluation.metrics.{key}")
        if not 0.0 <= value <= 1.0:
            raise ArtifactValidationError(f"evaluation.metrics.{key} must be between zero and one")
    baselines = _require_mapping(evaluation.get("baselines"), "evaluation.baselines")
    for key in ("majority_balanced_accuracy", "rule_balanced_accuracy"):
        _finite_number(baselines.get(key), f"evaluation.baselines.{key}")
    rule_context = _require_mapping(
        evaluation.get("rule_context"),
        "evaluation.rule_context",
    )
    if rule_context.get("meteorology") != "same_exact_single_run_surface_inputs":
        raise ArtifactValidationError("rule baseline does not use the reviewed weather inputs")
    if rule_context.get("viewing_mode") not in {"valley_fill", "peak_overlook"}:
        raise ArtifactValidationError("rule baseline viewing_mode is invalid")
    if not isinstance(rule_context.get("terrain_source"), str) or not rule_context.get(
        "terrain_source"
    ):
        raise ArtifactValidationError("rule baseline terrain_source is required")
    _finite_number(
        rule_context.get("elev_max_5km_m"),
        "evaluation.rule_context.elev_max_5km_m",
    )
    if not isinstance(rule_context.get("local_water"), bool):
        raise ArtifactValidationError("rule baseline local_water must be boolean")
    if (
        version == "cloudsea_logreg_operational_v2"
        and rule_context.get("spatial_meteorology")
        != "same_exact_single_run_valley_cells"
    ):
        raise ArtifactValidationError("v2 rule baseline lacks exact spatial weather")

    quality_gate = _require_mapping(evaluation.get("quality_gate"), "evaluation.quality_gate")
    checks = _require_mapping(quality_gate.get("checks"), "evaluation.quality_gate.checks")
    required_checks = ["time_ordered", "both_classes", "beats_majority", "beats_rule"]
    if version == "cloudsea_logreg_operational_v2":
        required_checks.append("rolling_stability")
    if quality_gate.get("passed") is not True or any(checks.get(key) is not True for key in required_checks):
        raise ArtifactValidationError("model quality gate did not pass")

    # Replace validated numeric fields so downstream inference never sees bools or exotic values.
    model["mean"] = mean
    model["scale"] = scale
    model["coef"] = coef
    model["intercept"] = intercept
    data["decision_threshold"] = threshold
    return data


def load_safe_artifact(
    path: Path,
    *,
    expected_spot_id: str | None = None,
    expected_viewpoint_id: str | None = None,
) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ArtifactValidationError(f"model artifact is unavailable: {exc}") from exc
    if path.is_symlink() or not path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_ARTIFACT_BYTES:
        raise ArtifactValidationError("model artifact size is invalid")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"model artifact is not valid JSON: {exc}") from exc
    return validate_safe_artifact(
        raw,
        expected_spot_id=expected_spot_id,
        expected_viewpoint_id=expected_viewpoint_id,
    )


def predict_artifact_probability(
    artifact: Mapping[str, Any],
    features: Mapping[str, float],
) -> float:
    names = artifact["feature_names"]
    model = artifact["model"]
    values = np.asarray([_finite_number(features.get(name, 0.0), name) for name in names])
    mean = np.asarray(model["mean"], dtype=float)
    scale = np.asarray(model["scale"], dtype=float)
    coef = np.asarray(model["coef"], dtype=float)
    logit = float(np.dot((values - mean) / scale, coef) + float(model["intercept"]))
    # Stable sigmoid avoids overflow for highly confident linear outputs.
    if logit >= 0:
        exp_term = math.exp(-logit)
        return 1.0 / (1.0 + exp_term)
    exp_term = math.exp(logit)
    return exp_term / (1.0 + exp_term)


def artifact_feature_contributions(
    artifact: Mapping[str, Any],
    features: Mapping[str, float],
) -> list[dict[str, float | str]]:
    names = artifact["feature_names"]
    model = artifact["model"]
    values = np.asarray([float(features.get(name, 0.0)) for name in names])
    standardized = (values - np.asarray(model["mean"])) / np.asarray(model["scale"])
    contributions = standardized * np.asarray(model["coef"])
    pairs = sorted(
        zip(names, contributions, strict=True),
        key=lambda item: abs(float(item[1])),
        reverse=True,
    )
    return [
        {"feature": name, "contribution": float(contribution)}
        for name, contribution in pairs
    ]
