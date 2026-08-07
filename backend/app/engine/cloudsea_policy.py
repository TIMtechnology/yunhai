"""Canonical post-rule cloud-sea policy shared by live inference and replay."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.dem import estimate_cloud_top_m
from app.adapters.open_meteo import estimate_cloud_base
from app.engine.cloudsea_ml import build_observational_factors, merge_ml_cloudsea_score
from app.engine.cloudsea_scorer import _classify_cloudsea_archetype, cloudsea_plausibility_cap
from app.engine.observable_field import compute_observable_field
from app.engine.water_context import water_fog_signal_hour
from app.models.schemas import PredictionScore


@dataclass(frozen=True)
class CloudseaPolicyResult:
    score: PredictionScore
    observable: dict
    archetype: str
    plausibility_cap: int
    water_fog_signal: float


def merge_cloudsea_policy_hour(
    rule_score: PredictionScore,
    *,
    ml_score: PredictionScore | None,
    spot_id: str | None,
    viewpoint_id: str | None,
    elevation: float,
    viewing_mode: str,
    terrain: dict | None,
    rh: float,
    cloud_low: float,
    cloud_mid: float,
    cloud_high: float,
    wind: float,
    precip_recent: float,
    temp: float,
    dewpoint: float,
    visibility: float | None,
    rh_850: float | None = None,
    rh_700: float | None = None,
    t_850: float | None = None,
    t_925: float | None = None,
    sunrise_azimuth_deg: float | None = None,
    sector_meteo: list[dict] | None = None,
    retrospective: bool = False,
) -> CloudseaPolicyResult:
    """Apply observational evidence, plausibility limits and ML fusion once."""
    terrain_ctx = terrain or {}
    cloud_base = estimate_cloud_base(temp, dewpoint)
    cloud_top = estimate_cloud_top_m(cloud_base, cloud_low, cloud_mid)
    elev_max_5km = float(terrain_ctx.get("elev_max_5km_m") or elevation)
    observable = compute_observable_field(
        viewer_elev_m=elevation,
        cloud_base_m=cloud_base,
        cloud_top_m=cloud_top,
        visibility_m=visibility,
        elev_profile_sunrise=terrain_ctx.get("elev_profile_sunrise"),
        viewing_mode=viewing_mode,
        rh_850=rh_850,
        rh_700=rh_700,
        sunrise_azimuth_deg=(
            sunrise_azimuth_deg or terrain_ctx.get("sunrise_azimuth_deg")
        ),
        elev_max_5km_m=elev_max_5km,
        sector_meteo=sector_meteo,
        summit_cloud_low=cloud_low,
        summit_rh=rh,
    )
    water_signal = water_fog_signal_hour(
        terrain_ctx.get("local_water"),
        elevation=elevation,
        rh=rh,
        temp=temp,
        dewpoint=dewpoint,
        wind=wind,
        cloud_high=cloud_high,
    )
    observational = build_observational_factors(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        cloud_high=cloud_high,
        visibility=visibility,
        rh=rh,
        rh_850=rh_850,
        rh_700=rh_700,
        t_850=t_850,
        t_925=t_925,
        wind=wind,
        precip_recent=precip_recent,
        elevation=elevation,
        water_fog_signal=water_signal,
    )
    archetype, _ = _classify_cloudsea_archetype(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        visibility=visibility,
        rh=rh,
        rh_850=rh_850,
        precip_recent=precip_recent,
        t_850=t_850,
        t_925=t_925,
        viewing_mode=viewing_mode,
        observable=observable,
        water_fog_signal=water_signal,
    )
    plausibility_cap = cloudsea_plausibility_cap(
        cloud_low=cloud_low,
        cloud_mid=cloud_mid,
        rh=rh,
        rh_850=rh_850,
        rh_700=rh_700,
        t_850=t_850,
        t_925=t_925,
        visibility=visibility,
        archetype=archetype,
        viewing_mode=viewing_mode,
        observable_fraction=float(observable.get("observable_fraction") or 0),
        elev_max_5km=elev_max_5km,
        elevation=elevation,
        cloud_base=cloud_base,
        water_fog_signal=water_signal,
    )

    if ml_score is not None:
        score = merge_ml_cloudsea_score(
            rule_score,
            ml_score,
            observational=observational,
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
            plausibility_cap=plausibility_cap,
            visibility=visibility,
            rh=rh,
            water_fog_signal=water_signal,
            cloud_low=cloud_low,
            cloud_mid=cloud_mid,
            retrospective=retrospective,
            rule_archetype=archetype,
        )
    else:
        score = PredictionScore(
            probability=rule_score.probability,
            grade=rule_score.grade,
            factors={
                **rule_score.factors,
                **{f"obs_{key}": value for key, value in observational.items()},
            },
            cloud_base_m=rule_score.cloud_base_m,
        )

    return CloudseaPolicyResult(
        score=score,
        observable=observable,
        archetype=archetype,
        plausibility_cap=plausibility_cap,
        water_fog_signal=water_signal,
    )
