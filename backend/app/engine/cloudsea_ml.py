from __future__ import annotations

import pickle
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.config import settings
from app.engine.cloudsea_features import DAY_FEATURE_NAMES, aggregate_day_features
from app.engine.utils import grade_from_probability
from app.models.schemas import FactorDetail, PredictionScore

_ARTIFACT: dict | None = None


def _model_path() -> Path:
    return Path(settings.cloudsea_model_path)


@lru_cache(maxsize=1)
def load_artifact() -> dict | None:
    path = _model_path()
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def ml_enabled() -> bool:
    return settings.cloudsea_ml_enabled and load_artifact() is not None


def _explain(model, day_feat: dict[str, float]) -> list[dict]:
    scaler = model.named_steps["scaler"]
    clf = model.named_steps["clf"]
    x = np.array([[day_feat[n] for n in DAY_FEATURE_NAMES]])
    xs = scaler.transform(x)[0]
    contribs = xs * clf.coef_[0]
    pairs = sorted(
        zip(DAY_FEATURE_NAMES, contribs),
        key=lambda p: abs(p[1]),
        reverse=True,
    )
    return [{"feature": n, "contribution": float(c)} for n, c in pairs[:5]]


def merge_ml_cloudsea_score(
    fuzzy: PredictionScore,
    ml: PredictionScore,
    *,
    observational: dict[str, FactorDetail] | None = None,
) -> PredictionScore:
    """ML 仅覆盖概率；保留规则引擎的气象因子与底层观测。"""
    merged: dict[str, FactorDetail] = {
        "ml_model": ml.factors["ml_model"],
        "fuzzy_reference": FactorDetail(
            score=fuzzy.probability / 100.0,
            weight=0.0,
            label="规则引擎参考",
            description="模糊逻辑专家系统评分，供与 ML 对照",
            value=f"{fuzzy.probability}%",
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
        probability=ml.probability,
        grade=grade_from_probability(ml.probability),
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
) -> PredictionScore | None:
    artifact = load_artifact()
    if not artifact:
        return None

    day_feat = aggregate_day_features(hour_rows, elevation=elevation)
    model = artifact["model"]
    x = np.array([[day_feat[n] for n in DAY_FEATURE_NAMES]])
    prob = float(model.predict_proba(x)[0, 1])
    probability = int(round(prob * 100))
    explains = _explain(model, day_feat)

    factors = {
        "ml_model": FactorDetail(
            score=prob,
            weight=1.0,
            label="ML 云海模型",
            description=f"基于 {artifact.get('n_days', '?')} 日标注训练的 Logistic 回归（LOOCV {artifact.get('loocv_accuracy', 0):.0%}）",
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
