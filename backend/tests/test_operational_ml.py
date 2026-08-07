from __future__ import annotations

import json
import asyncio
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from app.engine.operational_ml import (
    ArtifactValidationError,
    chronological_holdout_indices,
    load_safe_artifact,
    predict_artifact_probability,
)
from app.engine import cloudsea_ml
from app.adapters import open_meteo
from app.services import prediction_feedback, predictor
from app.services.meteo_backfill import precursor_hour_keys
from app.services.cloudsea_store import load_forecast_archive_precursor
from app.services.meteo_cache import (
    operational_precursor_rows_complete,
    overlay_hourly_with_operational_rows,
    precursor_rows_from_hourly,
)
from scripts.backfill_forecast_archive import (
    build_single_run_url,
    exact_single_run_archive_complete,
    single_run_initialization,
)
from scripts.train_operational_model import _exact_archive_dates
from app.services import curate_service


def _valid_artifact() -> dict:
    return {
        "schema_version": 1,
        "version": "cloudsea_logreg_operational_v1",
        "algorithm": "logistic_regression",
        "spot_id": "wunvshan",
        "viewpoint_id": "dianjiangtai",
        "window": "v7",
        "meteo_mode": "operational_single_run",
        "feature_names": ["rh_mean", "cloud_low_mean"],
        "model": {
            "mean": [80.0, 30.0],
            "scale": [10.0, 20.0],
            "coef": [0.8, -0.4],
            "intercept": -0.1,
            "classes": [0, 1],
        },
        "decision_threshold": 0.5,
        "trained_at": "2026-07-28T00:00:00+00:00",
        "n_days": 58,
        "training": {
            "date_start": "2024-10-05",
            "date_end": "2026-07-03",
            "positive_days": 18,
            "negative_days": 40,
            "data_sha256": "a" * 64,
            "source": "open_meteo_single_runs",
            "model": "ecmwf_ifs",
            "run_hour_utc": 0,
        },
        "evaluation": {
            "protocol": "chronological_holdout",
            "train_start": "2024-10-05",
            "train_end": "2026-06-11",
            "holdout_start": "2026-06-19",
            "holdout_end": "2026-07-03",
            "holdout_days": 15,
            "positive_days": 4,
            "negative_days": 11,
            "metrics": {
                "balanced_accuracy": 0.7,
                "recall": 0.8,
                "f1": 0.6,
                "brier": 0.18,
            },
            "baselines": {
                "majority_balanced_accuracy": 0.5,
                "rule_balanced_accuracy": 0.55,
            },
            "rule_context": {
                "meteorology": "same_exact_single_run_surface_inputs",
                "viewing_mode": "valley_fill",
                "terrain_source": "terrain_snapshot",
                "elev_max_5km_m": 787.0,
                "local_water": True,
            },
            "quality_gate": {
                "passed": True,
                "checks": {
                    "time_ordered": True,
                    "both_classes": True,
                    "beats_majority": True,
                    "beats_rule": True,
                },
            },
        },
    }


def test_single_run_backfill_uses_exact_00z_run_before_evening_decision() -> None:
    assert single_run_initialization("2026-06-19") == "2026-06-18T00:00"

    url = build_single_run_url(
        start=date(2026, 6, 18),
        lat=41.31976,
        lng=125.40773,
        run="2026-06-18T00:00",
    )

    assert url.startswith("https://single-runs-api.open-meteo.com/v1/forecast?")
    assert "run=2026-06-18T00%3A00" in url
    assert "models=ecmwf_ifs" in url
    assert "historical-forecast-api" not in url


def test_operational_inference_fetches_same_ecmwf_00z_single_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_get_json(url, *, params, **kwargs):
        captured.update({"url": url, "params": params, **kwargs})
        return {"hourly": {"time": []}}

    monkeypatch.setattr(open_meteo, "get_json", fake_get_json)
    monkeypatch.setattr(open_meteo, "cache_get", lambda _key: None)
    monkeypatch.setattr(open_meteo, "cache_set", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        open_meteo.fetch_operational_single_run(
            41.321319,
            125.424537,
            target_date=date(2026, 8, 7),
        )
    )

    assert result == {"hourly": {"time": []}}
    assert captured["url"] == "https://single-runs-api.open-meteo.com/v1/forecast"
    assert captured["params"]["run"] == "2026-08-06T00:00"
    assert captured["params"]["models"] == "ecmwf_ifs"
    assert captured["params"]["forecast_hours"] == 48
    assert "temperature_850hPa" not in captured["params"]["hourly"]


def test_operational_model_never_consumes_generic_forecast_rows() -> None:
    artifact = _valid_artifact()
    generic_rows = [{"time": "2026-08-07T03:00", "source": "best_match"}]
    exact_rows = [{"time": "2026-08-07T03:00", "source": "ecmwf_ifs_00z"}]

    assert predictor.select_ml_precursor_rows(
        artifact=artifact,
        target_date="2026-08-07",
        generic_rows=generic_rows,
        operational_rows_by_date={"2026-08-07": exact_rows},
    ) == exact_rows
    assert predictor.select_ml_precursor_rows(
        artifact=artifact,
        target_date="2026-08-07",
        generic_rows=generic_rows,
        operational_rows_by_date=None,
    ) == []

    legacy = dict(artifact, meteo_mode="historical_forecast")
    assert predictor.select_ml_precursor_rows(
        artifact=legacy,
        target_date="2026-08-07",
        generic_rows=generic_rows,
        operational_rows_by_date=None,
    ) == generic_rows


def test_prediction_snapshot_changes_when_operational_ml_input_changes() -> None:
    display = {
        "rows": [{"time": "2026-08-07T03:00", "rh": 80, "cloud_low": 10}],
        "fingerprint": "display-same",
    }
    exact = {
        **display,
        "ml_input": {
            "source": "open_meteo_single_runs:ecmwf_ifs:run=2026-08-06T00:00",
            "rows": [{"time": "2026-08-07T03:00", "rh": 95, "cloud_low": 70}],
        },
    }

    assert not prediction_feedback.meteo_snapshot_unchanged(display, exact)
    assert prediction_feedback.meteo_snapshot_unchanged(exact, exact)

    changed_run = json.loads(json.dumps(exact))
    changed_run["ml_input"]["source"] = (
        "open_meteo_single_runs:ecmwf_ifs:run=2026-08-06T06:00"
    )
    assert not prediction_feedback.meteo_snapshot_unchanged(exact, changed_run)


def test_operational_rows_override_the_entire_cloudsea_scoring_weather() -> None:
    hourly = {
        "time": ["2026-08-06T19:00", "2026-08-07T03:00"],
        "relative_humidity_2m": [70.0, 75.0],
        "cloud_cover_low": [5.0, 6.0],
        "cloud_cover_mid": [7.0, 8.0],
        "cloud_cover_high": [9.0, 10.0],
        "cloud_cover": [10.0, 11.0],
        "relative_humidity_850hPa": [45.0, 50.0],
    }
    exact_rows = [
        {
            "time": "2026-08-07T03:00",
            "rh": 96.0,
            "cloud_low": 80.0,
            "cloud_mid": 20.0,
            "cloud_high": 0.0,
            "rh_850": None,
        }
    ]

    scoring = overlay_hourly_with_operational_rows(
        hourly,
        {"2026-08-07": exact_rows},
    )

    assert scoring["relative_humidity_2m"] == [70.0, 96.0]
    assert scoring["cloud_cover_low"] == [5.0, 80.0]
    assert scoring["cloud_cover"] == [10.0, pytest.approx(100 / 3)]
    assert scoring["relative_humidity_850hPa"] == [45.0, None]
    assert hourly["relative_humidity_2m"] == [70.0, 75.0]


def test_incomplete_operational_window_is_rejected() -> None:
    rows = [
        {
            "time": key,
            "cloud_low": 10.0,
            "cloud_mid": 20.0,
            "cloud_high": 30.0,
            "visibility": 10_000.0,
            "rh": 85.0,
            "wind": 2.0,
            "temp": 15.0,
            "dewpoint": 13.0,
        }
        for key in precursor_hour_keys("2026-08-07")
    ]

    assert operational_precursor_rows_complete(rows, "2026-08-07")
    rows[-1]["visibility"] = None
    assert not operational_precursor_rows_complete(rows, "2026-08-07")

    times = precursor_hour_keys("2026-08-07")
    incomplete_hourly = {
        "time": times,
        "cloud_cover_mid": [20.0] * len(times),
        "cloud_cover_high": [30.0] * len(times),
        "visibility": [10_000.0] * len(times),
        "relative_humidity_2m": [85.0] * len(times),
        "wind_speed_10m": [2.0] * len(times),
        "temperature_2m": [15.0] * len(times),
        "dew_point_2m": [13.0] * len(times),
        "precipitation": [0.0] * len(times),
    }
    parsed = precursor_rows_from_hourly(incomplete_hourly, "2026-08-07")
    assert not operational_precursor_rows_complete(parsed, "2026-08-07")


def test_old_stitched_archive_is_not_treated_as_exact_single_run(tmp_path) -> None:
    db_path = tmp_path / "archive.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE meteo_forecast_archive (
                spot_id TEXT,
                viewpoint_id TEXT,
                target_date TEXT,
                issue_time TEXT,
                ts TEXT,
                source TEXT,
                raw_json TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO meteo_forecast_archive
            (spot_id, viewpoint_id, target_date, issue_time, ts, source, raw_json)
            VALUES ('wunvshan', 'dianjiangtai', '2026-06-19',
                    '2026-06-18T18:00:00+08:00', ?, 'historical_forecast', ?)
            """,
            [
                (timestamp, json.dumps({"time": timestamp, "rh": 90.0}))
                for timestamp in precursor_hour_keys("2026-06-19")
            ],
        )

    assert not exact_single_run_archive_complete(
        "wunvshan",
        "dianjiangtai",
        "2026-06-19",
        db_path=db_path,
    )
    assert load_forecast_archive_precursor(
        "wunvshan",
        "dianjiangtai",
        "2026-06-19",
        expected_source="open_meteo_single_runs:ecmwf_ifs:run=2026-06-18T00:00",
        db_path=db_path,
    ) == []


def test_training_requires_every_expected_hour_from_the_exact_run(tmp_path) -> None:
    db_path = tmp_path / "archive.db"
    target_date = "2026-06-19"
    issue_time = "2026-06-18T18:00:00+08:00"
    source = "open_meteo_single_runs:ecmwf_ifs:run=2026-06-18T00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE meteo_forecast_archive (
                spot_id TEXT,
                viewpoint_id TEXT,
                target_date TEXT,
                issue_time TEXT,
                ts TEXT,
                source TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO meteo_forecast_archive
            (spot_id, viewpoint_id, target_date, issue_time, ts, source)
            VALUES ('wunvshan', 'dianjiangtai', ?, ?, ?, ?)
            """,
            [
                (target_date, issue_time, f"2026-06-18T{hour:02d}:00", source)
                for hour in range(12)
            ],
        )

    assert target_date not in _exact_archive_dates(
        db_path,
        "wunvshan",
        "dianjiangtai",
    )


def test_chronological_holdout_never_trains_on_future_dates() -> None:
    dates = [f"2026-06-{day:02d}" for day in range(1, 21)]

    train_idx, holdout_idx = chronological_holdout_indices(dates, holdout_days=5)

    assert len(train_idx) == 15
    assert len(holdout_idx) == 5
    assert max(dates[i] for i in train_idx) < min(dates[i] for i in holdout_idx)


def test_safe_json_artifact_loads_and_predicts_without_pickle(tmp_path) -> None:
    path = tmp_path / "spot_wunvshan_dianjiangtai.json"
    path.write_text(json.dumps(_valid_artifact()), encoding="utf-8")

    artifact = load_safe_artifact(
        path,
        expected_spot_id="wunvshan",
        expected_viewpoint_id="dianjiangtai",
    )
    probability = predict_artifact_probability(
        artifact,
        {"rh_mean": 90.0, "cloud_low_mean": 10.0},
    )

    expected = 1.0 / (1.0 + np.exp(-1.1))
    assert probability == pytest.approx(expected)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a["evaluation"]["quality_gate"].update({"passed": False}),
        lambda a: a["evaluation"].update({"protocol": "leave_one_out"}),
        lambda a: a["model"].update({"coef": [0.8]}),
        lambda a: a["model"].update({"intercept": float("nan")}),
        lambda a: a["training"].update({"source": "historical_forecast_stitched"}),
        lambda a: a["evaluation"].pop("rule_context"),
    ],
)
def test_safe_artifact_rejects_unreviewed_or_malformed_models(tmp_path, mutate) -> None:
    artifact = _valid_artifact()
    mutate(artifact)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ArtifactValidationError):
        load_safe_artifact(
            path,
            expected_spot_id="wunvshan",
            expected_viewpoint_id="dianjiangtai",
        )


def test_safe_artifact_rejects_wrong_spot(tmp_path) -> None:
    path = tmp_path / "model.json"
    path.write_text(json.dumps(_valid_artifact()), encoding="utf-8")

    with pytest.raises(ArtifactValidationError):
        load_safe_artifact(
            path,
            expected_spot_id="donglingshan",
            expected_viewpoint_id="fengding",
        )


def test_admin_training_generates_reviewed_json_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "train_operational_model.py").write_text("# test", encoding="utf-8")
    output = tmp_path / "models" / "candidate.json"
    captured: list[str] = []

    def fake_run(args, **kwargs):
        captured.extend(str(value) for value in args)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(_valid_artifact()), encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(curate_service, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(curate_service.subprocess, "run", fake_run)

    result = curate_service.run_model_training(
        db_path=str(tmp_path / "cloudsea.db"),
        output_path=str(output),
    )

    assert result["status"] == "candidate_ready"
    assert result["deploy_recommended"] is True
    assert result["holdout_balanced_accuracy"] == pytest.approx(0.7)
    assert any(value.endswith("train_operational_model.py") for value in captured)
    assert not any(value.endswith(".pkl") for value in captured)


def test_tracked_production_artifact_passes_strict_loader() -> None:
    root = Path(__file__).resolve().parents[2]
    artifact = load_safe_artifact(
        root / "data" / "cloudsea" / "models" / "spot_wunvshan_dianjiangtai.json",
        expected_spot_id="wunvshan",
        expected_viewpoint_id="dianjiangtai",
    )

    assert artifact["n_days"] == 72
    assert artifact["evaluation"]["quality_gate"]["passed"] is True


def test_operational_model_falls_back_when_precursor_window_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    artifact = load_safe_artifact(
        root / "data" / "cloudsea" / "models" / "spot_wunvshan_dianjiangtai.json"
    )
    rows = [
        {
            "time": timestamp,
            "cloud_low": 20.0,
            "cloud_mid": 10.0,
            "cloud_high": 30.0,
            "visibility": 5000.0,
            "rh": 85.0,
            "wind": 2.0,
            "temp": 10.0,
            "dewpoint": 8.0,
            "precipitation": 0.0,
            "precip48": 0.0,
        }
        for timestamp in precursor_hour_keys("2026-07-03")
    ]
    monkeypatch.setattr(cloudsea_ml, "resolve_ml_artifact", lambda *_: artifact)

    complete = cloudsea_ml.predict_day_cloudsea(
        rows,
        spot_id="wunvshan",
        viewpoint_id="dianjiangtai",
        target_date="2026-07-03",
        precursor_rows=rows,
    )
    incomplete = cloudsea_ml.predict_day_cloudsea(
        rows[:-1],
        spot_id="wunvshan",
        viewpoint_id="dianjiangtai",
        target_date="2026-07-03",
        precursor_rows=rows[:-1],
    )

    assert complete is not None
    assert incomplete is None
