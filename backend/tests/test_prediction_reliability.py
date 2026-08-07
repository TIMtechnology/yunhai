from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from app.adapters import open_meteo
from app.adapters import sector_meteo
from app.engine.coord_transform import weather_coords
from app.engine.observable_field import compute_observable_field
from app.engine.cloudsea_scorer import score_cloudsea
from app.models.schemas import PredictRequest
from app.services import predictor
from app.services.prediction_freeze import freeze_probability
from app.services.spot_loader import load_spots
from app.services.terrain_store import resolve_baked_terrain


def test_curated_post_request_is_canonicalized_from_server_data() -> None:
    load_spots()
    supplied = PredictRequest(
        lat=0,
        lng=0,
        elevation=0,
        name="poisoned",
        spot_id="wunvshan",
        viewpoint_id="dianjiangtai",
    )

    canonical = predictor.canonicalize_predict_request(supplied)

    assert canonical.lat != 0
    assert canonical.lng != 0
    assert canonical.elevation != 0
    assert canonical.name != "poisoned"


def test_predict_request_requires_spot_and_viewpoint_together() -> None:
    with pytest.raises(ValidationError):
        PredictRequest(lat=30, lng=120, spot_id="wunvshan")


def test_open_meteo_waits_for_fallback_when_primary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*args, **kwargs):
        raise httpx.TimeoutException("primary failed")

    async def delayed_success(*args, **kwargs):
        await asyncio.sleep(0.01)
        return {"hourly": {"time": []}, "source": "historical"}

    monkeypatch.setattr(open_meteo, "cache_get", lambda *_: None)
    monkeypatch.setattr(open_meteo, "cache_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(open_meteo, "_fetch_forecast_primary", fail)
    monkeypatch.setattr(open_meteo, "_fetch_forecast_historical_window", delayed_success)

    result = asyncio.run(open_meteo.fetch_forecast(30, 120, 1))

    assert result["source"] == "historical"


def test_open_meteo_uses_stale_cache_when_both_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*args, **kwargs):
        raise httpx.TimeoutException("failed")

    def fake_cache_get(key: str):
        return {"source": "stale"} if key.endswith(":stale") else None

    monkeypatch.setattr(open_meteo, "cache_get", fake_cache_get)
    monkeypatch.setattr(open_meteo, "_fetch_forecast_primary", fail)
    monkeypatch.setattr(open_meteo, "_fetch_forecast_historical_window", fail)

    result = asyncio.run(open_meteo.fetch_forecast(30, 120, 1))

    assert result["source"] == "stale"


def test_elevation_failure_is_not_cached_as_sea_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*args, **kwargs):
        raise httpx.TimeoutException("failed")

    writes: list[tuple] = []
    monkeypatch.setattr(open_meteo, "cache_get", lambda *_: None)
    monkeypatch.setattr(open_meteo, "cache_set", lambda *args, **kwargs: writes.append(args))
    monkeypatch.setattr(open_meteo, "fetch_elevations_batch", fail)

    with pytest.raises(httpx.TimeoutException):
        asyncio.run(open_meteo.fetch_elevation(30, 120))

    assert writes == []


def test_retrospective_probability_is_stable_across_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage: dict[str, int] = {}
    monkeypatch.setattr("app.services.prediction_freeze.cache_get", storage.get)
    monkeypatch.setattr(
        "app.services.prediction_freeze.cache_set",
        lambda key, value, ttl: storage.setdefault(key, value),
    )

    first = freeze_probability("wunvshan:dianjiangtai", "2026-07-28T05:00", 72)
    second = freeze_probability("wunvshan:dianjiangtai", "2026-07-28T05:00", 21)

    assert first == 72
    assert second == 72


def test_baked_terrain_resolves_canonical_weather_coordinates() -> None:
    weather_lat, weather_lng = weather_coords(41.321319, 125.424537, "GCJ-02")

    base, profile = resolve_baked_terrain(
        spot_id="wunvshan",
        viewpoint_id="dianjiangtai",
        lat=weather_lat,
        lng=weather_lng,
        profile_date=date(2026, 8, 7),
    )

    assert base is not None
    assert profile
    assert base["source"] == "terrain_snapshot_baked"
    assert profile[0]["lat"] == pytest.approx(weather_lat, abs=0.002)
    assert profile[0]["lng"] == pytest.approx(weather_lng, abs=0.002)


def test_valley_mode_only_requests_spatial_weather_when_curated_rule_enables_it() -> None:
    times = ["2026-08-07T05:00"]

    assert predictor._needs_sector_meteo(
        "valley_fill",
        times,
        enable_valley=True,
    )
    assert not predictor._needs_sector_meteo(
        "valley_fill",
        times,
        enable_valley=False,
    )


def test_spatial_meteo_meta_distinguishes_active_from_point_fallback() -> None:
    active = predictor._spatial_meteo_meta(
        {
            "sector_meteo_source": "ecmwf_ifs_d_minus_1_00z",
            "sector_sample_points": [{}, {}, {}, {}],
            "sector_meteo_by_time": {
                "2026-08-07T03:00": [{}],
                "2026-08-07T04:00": [{}],
            },
        },
        enabled=True,
    )
    fallback = predictor._spatial_meteo_meta({}, enabled=True)

    assert active == {
        "enabled": True,
        "status": "active",
        "source": "ecmwf_ifs_d_minus_1_00z",
        "sample_points": 4,
        "available_hours": 2,
    }
    assert fallback == {
        "enabled": True,
        "status": "fallback_point_only",
        "source": None,
        "sample_points": 0,
        "available_hours": 0,
    }


def test_operational_valley_fetch_uses_the_same_exact_ecmwf_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_get_json(url, *, params, **kwargs):
        captured.update({"url": url, "params": params, **kwargs})
        return []

    monkeypatch.setattr(sector_meteo, "get_json", fake_get_json)
    monkeypatch.setattr(sector_meteo, "cache_get", lambda *_: None)
    monkeypatch.setattr(sector_meteo, "cache_set", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        sector_meteo.fetch_sector_operational_multi(
            [
                {"lat": 41.3128, "lng": 125.4472, "elev_m": 298},
                {"lat": 41.3005, "lng": 125.5048, "elev_m": 315},
            ],
            target_date=date(2026, 8, 7),
        )
    )

    assert result == []
    assert captured["url"] == open_meteo.SINGLE_RUN_FORECAST_URL
    assert captured["params"]["run"] == "2026-08-06T00:00"
    assert captured["params"]["models"] == "ecmwf_ifs"
    assert "daily" not in captured["params"]


def test_operational_valley_fetch_does_not_cache_an_empty_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple] = []

    async def empty_response(*_args, **_kwargs):
        return []

    monkeypatch.setattr(sector_meteo, "get_json", empty_response)
    monkeypatch.setattr(sector_meteo, "cache_get", lambda *_: None)
    monkeypatch.setattr(
        sector_meteo,
        "cache_set",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )

    result = asyncio.run(
        sector_meteo.fetch_sector_operational_multi(
            [{"lat": 41.30, "lng": 125.50, "elev_m": 300}],
            target_date=date(2026, 8, 7),
        )
    )

    assert result == []
    assert writes == []


def test_historical_valley_prediction_reuses_exact_spatial_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archived = {
        f"2026-08-07T{hour:02d}:00": [
            {
                "lat": 41.30,
                "lng": 125.50,
                "elev_m": 300,
                "cloud_low": 98,
                "rh": 99,
                "visibility": 100,
            }
        ]
        for hour in range(3, 7)
    }

    monkeypatch.setattr(
        predictor,
        "load_spatial_forecast_archive",
        lambda *_args, **_kwargs: archived,
    )

    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("complete archive must avoid a network refetch")

    monkeypatch.setattr(predictor, "fetch_sector_operational_multi", fail_fetch)
    terrain = {
        "elev_profile_sunrise": [
            {"lat": 41.30, "lng": 125.50, "distance_km": 5, "elev_m": 300}
        ],
        "sunrise_azimuth_deg": 72,
        "elev_curated_m": 804,
    }

    result = asyncio.run(
        predictor._load_sector_meteo_index(
            lat=41.319,
            lng=125.418,
            terrain=terrain,
            viewing_mode="valley_fill",
            enable_valley=True,
            operational_target_dates=[date(2026, 8, 7)],
            spot_id="wunvshan",
            viewpoint_id="dianjiangtai",
        )
    )

    assert result == archived
    assert terrain["sector_meteo_source"] == "ecmwf_ifs_d_minus_1_00z"


def test_valley_loader_never_leaks_a_d_minus_2_run_into_the_next_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        predictor,
        "load_spatial_forecast_archive",
        lambda *_args, **_kwargs: {},
    )

    async def fake_fetch(*_args, **_kwargs):
        return [{"hourly": {}}]

    mixed_index = {
        **{f"2026-08-07T{hour:02d}:00": [{"run": "d-1"}] for hour in range(3, 7)},
        **{f"2026-08-08T{hour:02d}:00": [{"run": "d-2"}] for hour in range(3, 7)},
    }
    monkeypatch.setattr(predictor, "fetch_sector_operational_multi", fake_fetch)
    monkeypatch.setattr(
        predictor,
        "build_sector_meteo_index",
        lambda *_args, **_kwargs: mixed_index,
    )
    monkeypatch.setattr(predictor, "save_spatial_forecast_archive", lambda **_kwargs: 4)
    terrain = {
        "elev_profile_sunrise": [
            {"lat": 41.30, "lng": 125.50, "distance_km": 5, "elev_m": 300}
        ],
        "sunrise_azimuth_deg": 72,
        "elev_curated_m": 804,
    }

    result = asyncio.run(
        predictor._load_sector_meteo_index(
            lat=41.319,
            lng=125.418,
            terrain=terrain,
            viewing_mode="valley_fill",
            enable_valley=True,
            operational_target_dates=[date(2026, 8, 7)],
            spot_id="wunvshan",
            viewpoint_id="dianjiangtai",
        )
    )

    assert set(result) == {
        f"2026-08-07T{hour:02d}:00" for hour in range(3, 7)
    }


def test_valley_observable_field_detects_cloud_below_a_clear_summit() -> None:
    sector = [
        {
            "elev_m": 300,
            "cloud_low": 98,
            "cloud_mid": 0,
            "rh": 100,
            "visibility": 100,
            "cloud_base_m": 300,
            "cloud_top_m": 650,
            "model_lat": 41.3005,
            "model_lng": 125.5093,
        }
    ]
    observed = compute_observable_field(
        viewer_elev_m=804,
        cloud_base_m=680,
        cloud_top_m=760,
        visibility_m=7000,
        elev_profile_sunrise=None,
        viewing_mode="valley_fill",
        elev_max_5km_m=824,
        sector_meteo=sector,
        summit_cloud_low=0,
        summit_rh=98,
    )

    assert observed["spatial_valley_cloud"] is True
    assert observed["sector_cloud_low_max"] == 98
    assert observed["sector_visibility_min_m"] == 100
    assert observed["observable_fraction"] >= 0.6

    score = score_cloudsea(
        rh=98,
        cloud_low=0,
        cloud_mid=0,
        cloud_high=0,
        wind=4.7,
        precip=0,
        precip_recent=0,
        temp=20,
        dewpoint=19.6,
        elevation=804,
        month=8,
        visibility=7000,
        viewing_mode="valley_fill",
        terrain={"elev_max_5km_m": 824},
        sector_meteo=sector,
        summit_cloud_low=0,
        summit_rh=98,
    )

    assert score.probability >= 58
    assert "空间差异型云海" in score.factors["low_cloud"].value
