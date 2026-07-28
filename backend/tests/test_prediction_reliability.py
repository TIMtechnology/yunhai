from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.adapters import open_meteo
from app.models.schemas import PredictRequest
from app.services import predictor
from app.services.prediction_freeze import freeze_probability
from app.services.spot_loader import load_spots


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
