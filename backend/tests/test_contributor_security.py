from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.services.cloudsea_store import init_store, upsert_label
from app.services.contributor_session import (
    issue_contributor_session,
    verify_contributor_session,
)


def test_contributor_session_rejects_tampering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "contributor_session_secret", "test-secret-at-least-32-bytes-long")

    contributor_id, token = issue_contributor_session()

    assert contributor_id.startswith("cid_")
    assert verify_contributor_session(token) == contributor_id
    with pytest.raises(ValueError, match="无效"):
        verify_contributor_session(token + "tampered")


def test_contributor_session_requires_server_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "contributor_session_secret", "")

    with pytest.raises(RuntimeError, match="CONTRIBUTOR_SESSION_SECRET"):
        issue_contributor_session()


def test_anonymous_contributor_cannot_overwrite_another_label(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "cloudsea.db"
    monkeypatch.setattr(settings, "cloudsea_db_path", str(db_path))
    monkeypatch.setattr(settings, "cloudsea_community_auto_approve", False)
    init_store()

    upsert_label(
        spot_id="spot",
        viewpoint_id="viewpoint",
        date_key="2026-07-01",
        status="full",
        contributor_id="cid_owner",
        labeled_by="cid_owner",
        review_status="pending",
        actor_contributor_id="cid_owner",
    )

    with pytest.raises(PermissionError, match="无权"):
        upsert_label(
            spot_id="spot",
            viewpoint_id="viewpoint",
            date_key="2026-07-01",
            status="none",
            contributor_id="cid_attacker",
            labeled_by="cid_attacker",
            review_status="pending",
            actor_contributor_id="cid_attacker",
        )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, contributor_id
            FROM cloudsea_labels
            WHERE spot_id='spot' AND viewpoint_id='viewpoint' AND date='2026-07-01'
            """
        ).fetchone()
    assert row == ("full", "cid_owner")


def test_community_labels_are_not_auto_approved_by_default() -> None:
    assert settings.cloudsea_community_auto_approve is False
    assert settings.cloudsea_curate_min_labels >= 3
