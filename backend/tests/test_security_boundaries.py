from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.schemas import PredictRequest
from app.routers import advisory, share
from app.services import prediction_feedback
from app.services import llm_advisory
from app.services.cache import cache_status
from app.services.client_ip import resolve_client_ip
from app.services.privacy import pseudonymize_ip
from app.services.rate_limit import reset_memory_rate_limits


def test_cors_rejects_unknown_origin() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "https://attacker.invalid",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert "access-control-allow-origin" not in response.headers


def test_security_headers_are_present() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "worker-src 'self' blob:" in csp
    assert "'unsafe-eval'" not in csp

    with TestClient(app) as client:
        main_response = client.get("/")
    assert "'unsafe-eval'" in main_response.headers["content-security-policy"]


def test_untrusted_host_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"Host": "attacker.invalid"})

    assert response.status_code == 400


def test_share_and_advisory_require_typed_prediction_payloads() -> None:
    with pytest.raises(ValidationError):
        share.ShareSnapshotRequest(
            date="2026-07-28",
            prediction={"hours": [{}]},
            privacy="show_everything",
        )
    with pytest.raises(ValidationError):
        advisory.DailyAdvisoryRequest(
            date="2026-07-28",
            prediction={"hours": [{}]},
        )


def test_request_body_over_limit_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/share/snapshot",
            content=b"x" * (2 * 1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413


def test_expensive_endpoint_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "expensive_rate_limit_per_minute", 2)
    reset_memory_rate_limits()
    with TestClient(app) as client:
        first = client.post("/api/advisory/daily-brief", json={})
        second = client.post("/api/advisory/daily-brief", json={})
        blocked = client.post("/api/advisory/daily-brief", json={})

    assert first.status_code == 422
    assert second.status_code == 422
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 60


def test_forwarded_ip_is_only_trusted_from_configured_proxy() -> None:
    assert (
        resolve_client_ip(
            direct_ip="198.51.100.20",
            forwarded_for="203.0.113.10",
            trusted_proxy_cidrs=["127.0.0.0/8"],
        )
        == "198.51.100.20"
    )
    assert (
        resolve_client_ip(
            direct_ip="127.0.0.1",
            forwarded_for="203.0.113.10, 127.0.0.1",
            trusted_proxy_cidrs=["127.0.0.0/8"],
        )
        == "203.0.113.10"
    )


def test_analytics_ip_is_pseudonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "analytics_ip_salt", "analytics-test-salt-at-least-32-bytes")
    value = pseudonymize_ip("203.0.113.10")

    assert value.startswith("ip_")
    assert "203.0.113.10" not in value
    assert value == pseudonymize_ip("203.0.113.10")


def test_llm_failure_does_not_expose_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    async def fail(*args, **kwargs):
        raise RuntimeError("upstream-secret-detail")

    monkeypatch.setattr(settings, "llm_advisory_enabled", True)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_advisory, "post_json", fail)
    monkeypatch.setattr(llm_advisory, "cache_get", lambda *_: None)
    result = asyncio.run(
        llm_advisory.generate_daily_brief(
                {
                    "location": {"lat": 30, "lng": 120},
                    "hours": [
                        {
                            "time": "2026-07-28T05:00",
                            "cloudsea": {"probability": 20, "grade": "低", "factors": {}},
                            "sunrise": {"probability": 30, "grade": "低", "factors": {}},
                            "weather": {
                                "temperature": 20,
                                "humidity": 70,
                                "precipitation": 0,
                                "cloud_cover": 20,
                                "cloud_cover_low": 10,
                                "cloud_cover_mid": 20,
                                "cloud_cover_high": 30,
                                "wind_speed": 2,
                                "weather_text": "晴",
                            },
                            "scenario": {
                                "code": "low",
                                "label": "一般",
                                "narrative": "",
                                "level": 1,
                                "combined_score": 25,
                            },
                            "is_sunrise_window": True,
                        }
                    ],
                "days": [],
                "best_windows": {},
            },
            "2026-07-28",
        )
    )

    assert result["error"] == "upstream_unavailable"
    assert "upstream-secret-detail" not in str(result)


def test_llm_context_contains_server_owned_geography_for_independent_assessment() -> None:
    context = llm_advisory.build_advisory_context(
        {
            "location": {
                "lat": 41.321319,
                "lng": 125.424537,
                "elevation": 804,
                "name": "本溪五女山 · 点将台",
                "spot_id": "wunvshan",
                "viewpoint_id": "dianjiangtai",
                "viewing_mode": "valley_fill",
                "terrain": {
                    "elev_max_5km_m": 824,
                    "relief_5km_m": 520,
                    "sunrise_azimuth_deg": 68,
                },
            },
            "hours": [],
            "days": [],
        },
        "2026-08-07",
    )

    geography = context["site_geography"]
    assert geography["coordinates"]["map_gcj02"] == {
        "lat": 41.321319,
        "lng": 125.424537,
    }
    assert geography["coordinates"]["weather_wgs84"]["lng"] != 125.424537
    assert geography["local_water"]["name"].startswith("桓龙湖")
    assert geography["water_to_viewpoint_height_m"] == pytest.approx(504)
    assert "点位地形与水体独立研判" in llm_advisory.SYSTEM_PROMPT
    assert "不得只复述平台分数" in llm_advisory.SYSTEM_PROMPT


def test_health_cache_status_does_not_expose_redis_url() -> None:
    assert "redis_url" not in cache_status()


def test_predict_request_validates_location_and_size_bounds() -> None:
    with pytest.raises(ValidationError):
        PredictRequest(lat=91, lng=120)
    with pytest.raises(ValidationError):
        PredictRequest(lat=30, lng=181)
    with pytest.raises(ValidationError):
        PredictRequest(lat=30, lng=120, hours=1000)
    with pytest.raises(ValidationError):
        PredictRequest(lat=30, lng=120, name="x" * 201)


def test_share_page_never_embeds_untrusted_script_or_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = "</script><script>globalThis.pwned=true</script><img src=x onerror=alert(1)>"
    snapshot = {
        "location": {"display_name": payload},
        "target": {"date": "2026-07-28", "weekday": "二", "sunrise_time": "05:00"},
        "scores": {},
        "evidence": {
            "hourly_evidence_table": [{"time": payload}],
            "meteo_analysis_hints": [payload],
        },
        "og_image_url": "/safe.png",
    }
    monkeypatch.setattr(share, "get_share_snapshot", lambda _: snapshot)

    response = asyncio.run(share.share_page("sh_safe"))
    html = response.body.decode("utf-8")

    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_public_history_does_not_reconcile_or_expose_contributor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []
    monkeypatch.setattr(
        prediction_feedback,
        "get_label",
        lambda *_: {
            "status": "full",
            "notes": "private note",
            "contributor_id": "cid_private",
            "review_status": "approved",
        },
    )
    monkeypatch.setattr(
        prediction_feedback,
        "reconcile_target_date",
        lambda *args, **kwargs: writes.append("write"),
    )
    monkeypatch.setattr(prediction_feedback, "list_prediction_access_logs", lambda **_: [])
    monkeypatch.setattr(prediction_feedback, "load_label_precursor_meteo", lambda *_: [])

    result = prediction_feedback.get_public_prediction_history(
        "spot", "viewpoint", "2026-07-01"
    )

    assert writes == []
    assert result["label"] == {"status": "full", "review_status": "approved"}
