from __future__ import annotations

import pytest

from app.config import settings
from app.engine import cloudsea_ml


def test_pickle_model_is_not_loaded_without_explicit_legacy_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    model_path = tmp_path / "spot_example_viewpoint.pkl"
    model_path.write_bytes(b"not-a-real-pickle")
    monkeypatch.setattr(settings, "cloudsea_ml_enabled", True)
    monkeypatch.setattr(settings, "cloudsea_allow_legacy_pickle_models", False)
    monkeypatch.setattr(cloudsea_ml, "spot_model_path", lambda *_: model_path)
    monkeypatch.setattr(
        cloudsea_ml.pickle,
        "load",
        lambda *_: (_ for _ in ()).throw(AssertionError("pickle.load must not run")),
    )
    cloudsea_ml.load_spot_artifact.cache_clear()

    assert cloudsea_ml.load_spot_artifact("example", "viewpoint") is None
