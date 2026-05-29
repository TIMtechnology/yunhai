from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import settings

TZ = ZoneInfo("Asia/Shanghai")

GOLD_LABELS = [
    ("wunvshan", "dianjiangtai", "2026-05-04", "full", "用户确认有云海"),
    ("wunvshan", "dianjiangtai", "2026-05-09", "full", "用户确认有云海"),
    ("wunvshan", "dianjiangtai", "2026-05-22", "full", "用户确认有云海"),
    ("wunvshan", "dianjiangtai", "2026-05-29", "full", "用户确认有云海"),
    ("wunvshan", "dianjiangtai", "2026-05-28", "none", "用户确认无云海"),
    ("wunvshan", "dianjiangtai", "2026-05-24", "none", "用户确认无云海"),
    ("wunvshan", "dianjiangtai", "2026-05-25", "none", "用户确认无云海"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    path = Path(settings.cloudsea_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meteo_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spot_id TEXT,
                viewpoint_id TEXT,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                elevation REAL,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(lat, lng, ts, source)
            );

            CREATE TABLE IF NOT EXISTS cloudsea_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spot_id TEXT NOT NULL,
                viewpoint_id TEXT NOT NULL,
                date TEXT NOT NULL,
                window_start INTEGER NOT NULL DEFAULT 3,
                window_end INTEGER NOT NULL DEFAULT 7,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'confirmed',
                notes TEXT,
                labeled_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(spot_id, viewpoint_id, date, window_start, window_end)
            );

            CREATE TABLE IF NOT EXISTS prediction_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label_id INTEGER,
                date TEXT NOT NULL,
                spot_id TEXT,
                viewpoint_id TEXT,
                model_version TEXT NOT NULL,
                hours_json TEXT NOT NULL,
                metrics_json TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        for spot_id, viewpoint_id, date_key, status, notes in GOLD_LABELS:
            conn.execute(
                """
                INSERT OR IGNORE INTO cloudsea_labels
                (spot_id, viewpoint_id, date, window_start, window_end, status,
                 confidence, notes, labeled_by, created_at, updated_at)
                VALUES (?, ?, ?, 3, 7, ?, 'confirmed', ?, 'seed', ?, ?)
                """,
                (spot_id, viewpoint_id, date_key, status, notes, _now_iso(), _now_iso()),
            )


def upsert_label(
    *,
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    status: str,
    window_start: int = 3,
    window_end: int = 7,
    confidence: str = "confirmed",
    notes: str = "",
    labeled_by: str = "manual",
) -> dict[str, Any]:
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO cloudsea_labels
            (spot_id, viewpoint_id, date, window_start, window_end, status,
             confidence, notes, labeled_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(spot_id, viewpoint_id, date, window_start, window_end)
            DO UPDATE SET
                status=excluded.status,
                confidence=excluded.confidence,
                notes=excluded.notes,
                labeled_by=excluded.labeled_by,
                updated_at=excluded.updated_at
            """,
            (
                spot_id,
                viewpoint_id,
                date_key,
                window_start,
                window_end,
                status,
                confidence,
                notes,
                labeled_by,
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=? AND date=?
              AND window_start=? AND window_end=?
            """,
            (spot_id, viewpoint_id, date_key, window_start, window_end),
        ).fetchone()
    return dict(row) if row else {}


def get_label(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    window_start: int = 3,
    window_end: int = 7,
) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=? AND date=?
              AND window_start=? AND window_end=?
            """,
            (spot_id, viewpoint_id, date_key, window_start, window_end),
        ).fetchone()
    return dict(row) if row else None


def list_labels(
    *,
    spot_id: Optional[str] = None,
    viewpoint_id: Optional[str] = None,
    month: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if spot_id:
        clauses.append("spot_id=?")
        params.append(spot_id)
    if viewpoint_id:
        clauses.append("viewpoint_id=?")
        params.append(viewpoint_id)
    if month:
        clauses.append("date LIKE ?")
        params.append(f"{month}-%")
    sql = f"SELECT * FROM cloudsea_labels WHERE {' AND '.join(clauses)} ORDER BY date DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def calendar_summary(spot_id: str, viewpoint_id: str, month: str) -> list[dict[str, Any]]:
    labels = list_labels(spot_id=spot_id, viewpoint_id=viewpoint_id, month=month)
    return [{"date": r["date"], "status": r["status"]} for r in labels]


def save_meteo_hourly(
    *,
    spot_id: Optional[str],
    viewpoint_id: Optional[str],
    lat: float,
    lng: float,
    elevation: Optional[float],
    ts: str,
    source: str,
    raw: dict[str, Any],
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO meteo_hourly
            (spot_id, viewpoint_id, lat, lng, elevation, ts, source, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spot_id,
                viewpoint_id,
                lat,
                lng,
                elevation,
                ts,
                source,
                json.dumps(raw, ensure_ascii=False),
                _now_iso(),
            ),
        )


def save_prediction_run(
    *,
    date_key: str,
    spot_id: Optional[str],
    viewpoint_id: Optional[str],
    model_version: str,
    hours: list[dict[str, Any]],
    label_id: Optional[int] = None,
    metrics: Optional[dict[str, Any]] = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO prediction_runs
            (label_id, date, spot_id, viewpoint_id, model_version, hours_json, metrics_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                label_id,
                date_key,
                spot_id,
                viewpoint_id,
                model_version,
                json.dumps(hours, ensure_ascii=False),
                json.dumps(metrics, ensure_ascii=False) if metrics else None,
                _now_iso(),
            ),
        )
        return int(cur.lastrowid)


def _meteo_for_date(spot_id: str, viewpoint_id: str, date_key: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM meteo_hourly
            WHERE spot_id=? AND viewpoint_id=? AND ts LIKE ?
              AND source='historical_forecast'
            ORDER BY ts
            """,
            (spot_id, viewpoint_id, f"{date_key}T%"),
        ).fetchall()
    return [dict(r) for r in rows]
