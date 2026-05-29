from __future__ import annotations

import pickle
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.config import settings
from app.engine.cloudsea_features import DAY_FEATURE_NAMES, aggregate_day_features
from app.engine.ml_eligibility import build_ml_status, spot_model_path
from app.engine.utils import grade_from_probability
from app.models.schemas import FactorDetail, PredictionScore


def _default_model_path() -> Path:
    return Path(settings.cloudsea_model_path)


@lru_cache(maxsize=1)
def load_default_artifact() -> dict | None:
    path = _default_model_path()
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


@lru_cache(maxsize=64)
def load_spot_artifact(spot_id: str, viewpoint_id: str) -> dict | None:
    path = spot_model_path(spot_id, viewpoint_id)
    if path.is_file():
        with open(path, "rb") as f:
            return pickle.load(f)
    if spot_id == "wunvshan":
        return load_default_artifact()
    return None


def ml_enabled() -> bool:
    return settings.cloudsea_ml_enabled and load_default_artifact() is not None


def get_ml_status(spot_id: str | None, viewpoint_id: str | None) -> dict:
    has_spot_model = False
    model_trained_days = 0
    if spot_id and viewpoint_id:
        has_spot_model = spot_model_path(spot_id, viewpoint_id).is_file()
        artifact = None
        if has_spot_model:
            try:
                import pickle

                with open(spot_model_path(spot_id, viewpoint_id), "rb") as f:
                    artifact = pickle.load(f)
            except Exception:
                artifact = None
        if spot_id == "wunvshan" and not has_spot_model:
            default_path = _default_model_path()
            has_spot_model = default_path.is_file()
            if has_spot_model:
                try:
                    import pickle

                    with open(default_path, "rb") as f:
                        artifact = pickle.load(f)
                except Exception:
                    artifact = None
        if artifact:
            model_trained_days = int(artifact.get("n_days") or 0)
    return build_ml_status(
        spot_id,
        viewpoint_id,
        has_spot_model=has_spot_model,
        model_trained_days=model_trained_days,
    )


def should_use_spot_ml(spot_id: str | None, viewpoint_id: str | None) -> bool:
    if not settings.cloudsea_ml_enabled:
        return False
    status = get_ml_status(spot_id, viewpoint_id)
    return bool(status.get("ml_active"))


def resolve_ml_artifact(spot_id: str | None, viewpoint_id: str | None) -> dict | None:
    if not should_use_spot_ml(spot_id, viewpoint_id):
        return None
    if not spot_id or not viewpoint_id:
        return None
    return load_spot_artifact(spot_id, viewpoint_id)


def ml_calibration_weight(spot_id: str | None) -> float:
    """点位 ML 已启用时，按景区设定融合权重。"""
    if spot_id == "wunvshan":
        return 0.80
    if spot_id:
        return 0.75
    return 0.45


def _explain(
    model,
    day_feat: dict[str, float],
    feature_names: list[str] | None = None,
) -> list[dict]:
    names = feature_names or DAY_FEATURE_NAMES
    scaler = model.named_steps["scaler"]
    clf = model.named_steps["clf"]
    x = np.array([[day_feat.get(n, 0.0) for n in names]])
    xs = scaler.transform(x)[0]
    contribs = xs * clf.coef_[0]
    pairs = sorted(
        zip(names, contribs),
        key=lambda p: abs(p[1]),
        reverse=True,
    )
    return [{"feature": n, "contribution": float(c)} for n, c in pairs[:5]]


def merge_ml_cloudsea_score(
    fuzzy: PredictionScore,
    ml: PredictionScore,
    *,
    observational: dict[str, FactorDetail] | None = None,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
    plausibility_cap: int | None = None,
) -> PredictionScore:
    """ML 与规则融合：仅在本点位 ML 已启用时混合。"""
    ml_weight = ml_calibration_weight(spot_id)
    fuzzy_prob = fuzzy.probability
    ml_prob = ml.probability
    blended = int(round(ml_weight * ml_prob + (1.0 - ml_weight) * fuzzy_prob))
    if plausibility_cap is not None:
        blended = min(blended, plausibility_cap)
    blended = max(0, min(100, blended))

    status = get_ml_status(spot_id, viewpoint_id)
    mode_note = {
        "wunvshan_model": "五女山校准模型",
        "spot_model": "本点位专属模型",
    }.get(str(status.get("mode")), "ML 模型")

    artifact_ref = ml.factors["ml_model"].reference
    merged: dict[str, FactorDetail] = {
        "ml_model": FactorDetail(
            score=ml_prob / 100.0,
            weight=ml_weight,
            label="ML 云海模型",
            description=(
                f"{mode_note}；本点 ML 权重 {ml_weight:.0%}，"
                f"与规则 {fuzzy_prob}% 融合为 {blended}%"
                + (f"（观测场上限 {plausibility_cap}%）" if plausibility_cap is not None else "")
            ),
            value=f"P_ml={ml_prob}% → P={blended}%",
            reference=artifact_ref,
        ),
        "fuzzy_reference": FactorDetail(
            score=fuzzy_prob / 100.0,
            weight=round(1.0 - ml_weight, 2),
            label="规则引擎参考",
            description="模糊逻辑专家系统评分，与 ML 按点位权重融合",
            value=f"{fuzzy_prob}%",
            reference="fuzzy_v2_archetype",
        ),
    }
    for key, detail in ml.factors.items():
        if key.startswith("ml_factor"):
            merged[key] = detail

    merged.update(fuzzy.factors)

    if observational:
        for key, detail in observational.items():
            merged[f"obs_{key}"] = detail

    return PredictionScore(
        probability=blended,
        grade=grade_from_probability(blended),
        factors=merged,
        cloud_base_m=fuzzy.cloud_base_m,
    )


def build_observational_factors(
    *,
    cloud_low: float,
    cloud_mid: float,
    cloud_high: float,
    visibility: float | None,
    rh: float,
    rh_850: float | None,
    rh_700: float | None,
    t_850: float | None,
    t_925: float | None,
    wind: float,
    precip_recent: float,
    elevation: float,
) -> dict[str, FactorDetail]:
    """当前时刻底层气象观测（不参与加权，仅供专业展示）。"""
    from app.engine.cloudsea_scorer import _classify_cloudsea_archetype

    archetype, archetype_note = _classify_cloudsea_archetype(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        rh=rh,
        rh_850=rh_850,
        precip_recent=precip_recent,
        t_850=t_850,
        t_925=t_925,
    )
    inversion_value = "—"
    if t_850 is not None and t_925 is not None:
        inversion_value = f"ΔT={t_850 - t_925:+.1f}°C"

    vis_value = f"{visibility / 1000:.1f} km" if visibility is not None else "—"

    return {
        "pattern": FactorDetail(
            score=1.0 if archetype in ("type_a", "type_b") else 0.3,
            weight=0.0,
            label="云海型态",
            description=archetype_note or "未命中 Type A/B 典型型态",
            value={
                "type_a": "Type A 高能见度山谷",
                "type_b": "Type B 低能见度层云",
                "fog_exclude": "雾型排除",
                "neutral": "中性",
            }.get(archetype, archetype),
            reference="wunvshan_gold_standard",
        ),
        "cloud_layers": FactorDetail(
            score=min((cloud_low + cloud_mid + cloud_high) / 100.0, 1.0),
            weight=0.0,
            label="分层云量",
            description="Open-Meteo 低/中/高云量（模式网格）",
            value=f"低{cloud_low:.0f}% · 中{cloud_mid:.0f}% · 高{cloud_high:.0f}%",
            reference="open_meteo",
        ),
        "visibility_raw": FactorDetail(
            score=min((visibility or 10000) / 20000.0, 1.0),
            weight=0.0,
            label="能见度观测",
            description="2 m 高度能见度（Historical Forecast）",
            value=vis_value,
            reference="open_meteo",
        ),
        "pressure_profile": FactorDetail(
            score=0.5,
            weight=0.0,
            label="垂直场剖面",
            description="850/700 hPa 湿度与 925–850 hPa 温差",
            value=(
                f"RH850={rh_850:.0f}% · RH700={rh_700:.0f}% · {inversion_value}"
                if rh_850 is not None and rh_700 is not None
                else "气压层数据缺失"
            ),
            reference="open_meteo_pressure_level",
        ),
        "surface_state": FactorDetail(
            score=min(rh / 100.0, 1.0),
            weight=0.0,
            label="地面态",
            description="近地面湿度、风速与近48h降水",
            value=f"RH2m={rh:.0f}% · 风{wind:.1f}m/s · 48h降水{precip_recent:.1f}mm",
            reference="open_meteo",
        ),
        "elevation": FactorDetail(
            score=0.5,
            weight=0.0,
            label="观景点海拔",
            description="景区观景点海拔（WGS84）",
            value=f"{elevation:.0f} m",
            reference="scenic_spot",
        ),
    }


def predict_day_cloudsea(
    hour_rows: list[dict],
    *,
    elevation: float = 804.0,
    cloud_base_m: float = 0.0,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
    terrain: dict | None = None,
) -> PredictionScore | None:
    artifact = resolve_ml_artifact(spot_id, viewpoint_id)
    if not artifact:
        return None

    feature_names = artifact.get("feature_names") or DAY_FEATURE_NAMES
    use_observable = any(n in feature_names for n in ("observable_fraction_mean",))
    day_feat = aggregate_day_features(
        hour_rows,
        elevation=elevation,
        terrain=terrain,
        use_observable_field=use_observable,
    )
    model = artifact["model"]
    x = np.array([[day_feat.get(n, 0.0) for n in feature_names]])
    prob = float(model.predict_proba(x)[0, 1])
    probability = int(round(prob * 100))
    explains = _explain(model, day_feat, feature_names)

    factors = {
        "ml_model": FactorDetail(
            score=prob,
            weight=1.0,
            label="ML 云海模型",
            description=(
                f"基于 {artifact.get('n_days', '?')} 日有效标注训练"
                f"（LOOCV {artifact.get('loocv_accuracy', 0):.0%}）"
            ),
            value=f"P={probability}%",
            reference=artifact.get("version", "cloudsea_ml"),
        ),
    }
    for i, item in enumerate(explains[:3]):
        factors[f"ml_factor_{i+1}"] = FactorDetail(
            score=min(abs(item["contribution"]), 1.0),
            weight=0.0,
            label=item["feature"],
            description="标准化特征贡献（方向）",
            value=f"{item['contribution']:+.2f}",
            reference=artifact.get("version", "cloudsea_ml"),
        )

    return PredictionScore(
        probability=probability,
        grade=grade_from_probability(probability),
        factors=factors,
        cloud_base_m=cloud_base_m,
    )
