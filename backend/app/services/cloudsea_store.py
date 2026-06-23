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
            CREATE TABLE IF NOT EXISTS meteo_day_cache (
                spot_id TEXT NOT NULL,
                viewpoint_id TEXT NOT NULL,
                date_key TEXT NOT NULL,
                source TEXT NOT NULL,
                hourly_json TEXT NOT NULL,
                astronomy_json TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (spot_id, viewpoint_id, date_key, source)
            );

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

            CREATE TABLE IF NOT EXISTS meteo_forecast_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spot_id TEXT NOT NULL,
                viewpoint_id TEXT NOT NULL,
                target_date TEXT NOT NULL,
                issue_time TEXT NOT NULL,
                ts TEXT NOT NULL,
                lead_hours REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'historical_forecast',
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(spot_id, viewpoint_id, target_date, issue_time, ts)
            );

            CREATE INDEX IF NOT EXISTS idx_forecast_archive_lookup
                ON meteo_forecast_archive(spot_id, viewpoint_id, target_date, issue_time);

            CREATE TABLE IF NOT EXISTS prediction_access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                target_date TEXT NOT NULL,
                lead_hours_to_dawn REAL,
                spot_id TEXT,
                viewpoint_id TEXT,
                location_id TEXT,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                elevation REAL,
                page_source TEXT,
                client_id TEXT,
                model_version TEXT,
                data_source TEXT NOT NULL DEFAULT 'live_forecast',
                prediction_json TEXT NOT NULL,
                meteo_snapshot_json TEXT NOT NULL,
                feature_snapshot_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_pal_spot_date
                ON prediction_access_log(spot_id, viewpoint_id, target_date);
            CREATE INDEX IF NOT EXISTS idx_pal_created
                ON prediction_access_log(created_at);

            CREATE TABLE IF NOT EXISTS prediction_access_outcome (
                access_log_id INTEGER PRIMARY KEY REFERENCES prediction_access_log(id),
                reconciled_at TEXT NOT NULL,
                label_status TEXT,
                label_id INTEGER,
                actual_meteo_json TEXT,
                forecast_error_json TEXT,
                predicted_positive INTEGER,
                label_positive INTEGER,
                direction_ok INTEGER,
                diagnosis_json TEXT
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
    from app.services.community_store import migrate_community_schema

    migrate_community_schema()


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
    location_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    location_name: Optional[str] = None,
    contributor_id: Optional[str] = None,
    review_status: str = "approved",
    sunrise_quality: Optional[str] = None,
) -> dict[str, Any]:
    now = _now_iso()
    with _connect() as conn:
        existing_row = conn.execute(
            """
            SELECT * FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=? AND date=?
              AND window_start=? AND window_end=?
            """,
            (spot_id, viewpoint_id, date_key, window_start, window_end),
        ).fetchone()
        existing = dict(existing_row) if existing_row else None
        prev_status = existing.get("review_status") if existing else None
        becoming_approved = review_status == "approved" and prev_status != "approved"
        conn.execute(
            """
            INSERT INTO cloudsea_labels
            (spot_id, viewpoint_id, date, window_start, window_end, status,
             confidence, notes, labeled_by, created_at, updated_at,
             location_id, lat, lng, location_name, contributor_id,
             review_status, reviewed_at, reviewed_by, sunrise_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(spot_id, viewpoint_id, date, window_start, window_end)
            DO UPDATE SET
                status=excluded.status,
                confidence=excluded.confidence,
                notes=excluded.notes,
                labeled_by=excluded.labeled_by,
                updated_at=excluded.updated_at,
                location_id=COALESCE(excluded.location_id, cloudsea_labels.location_id),
                lat=COALESCE(excluded.lat, cloudsea_labels.lat),
                lng=COALESCE(excluded.lng, cloudsea_labels.lng),
                location_name=COALESCE(excluded.location_name, cloudsea_labels.location_name),
                contributor_id=COALESCE(excluded.contributor_id, cloudsea_labels.contributor_id),
                review_status=excluded.review_status,
                sunrise_quality=excluded.sunrise_quality,
                reviewed_at=CASE
                    WHEN excluded.review_status='approved' THEN excluded.reviewed_at
                    ELSE cloudsea_labels.reviewed_at
                END,
                reviewed_by=CASE
                    WHEN excluded.review_status='approved' THEN excluded.reviewed_by
                    ELSE cloudsea_labels.reviewed_by
                END
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
                location_id,
                lat,
                lng,
                location_name,
                contributor_id,
                review_status,
                now if review_status == "approved" else None,
                labeled_by if review_status == "approved" else None,
                sunrise_quality,
            ),
        )
        if contributor_id and not existing:
            from app.services.community_store import increment_daily_quota, _update_trust_level

            increment_daily_quota(conn, contributor_id)
            conn.execute(
                """
                UPDATE contributors SET label_count_total = label_count_total + 1
                WHERE id=?
                """,
                (contributor_id,),
            )
            if location_id:
                from app.services.community_store import increment_location_label_count

                increment_location_label_count(
                    conn,
                    location_id,
                    approved_delta=1 if review_status == "approved" else 0,
                )
            if review_status == "approved":
                conn.execute(
                    """
                    UPDATE contributors
                    SET label_count_approved = label_count_approved + 1
                    WHERE id=?
                    """,
                    (contributor_id,),
                )
                _update_trust_level(conn, contributor_id)
        elif contributor_id and becoming_approved:
            from app.services.community_store import _update_trust_level, increment_location_label_count

            if location_id:
                increment_location_label_count(
                    conn,
                    location_id,
                    approved_delta=1,
                    label_delta=0,
                )
            conn.execute(
                """
                UPDATE contributors
                SET label_count_approved = label_count_approved + 1
                WHERE id=?
                """,
                (contributor_id,),
            )
            _update_trust_level(conn, contributor_id)
        row = conn.execute(
            """
            SELECT * FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=? AND date=?
              AND window_start=? AND window_end=?
            """,
            (spot_id, viewpoint_id, date_key, window_start, window_end),
        ).fetchone()
    result = dict(row) if row else {}
    if result and review_status in (None, "approved"):
        try:
            from app.services.meteo_backfill import ensure_label_meteo_cached

            ensure_label_meteo_cached(result)
        except Exception:
            pass
    return result


def list_approved_labels() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM cloudsea_labels
            WHERE review_status='approved' OR review_status IS NULL
            ORDER BY date
            """
        ).fetchall()
    return [dict(r) for r in rows]


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
    return [
        {
            "date": r["date"],
            "status": r["status"],
            "sunrise_quality": r.get("sunrise_quality"),
        }
        for r in labels
    ]


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


def save_meteo_day_cache(
    *,
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    source: str,
    hourly: dict[str, Any],
    astronomy: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> None:
    """缓存整日 hourly + astronomy，标注回放可跳过 Open-Meteo 历史 API。"""
    path = db_path or Path(settings.cloudsea_db_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO meteo_day_cache
            (spot_id, viewpoint_id, date_key, source, hourly_json, astronomy_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spot_id,
                viewpoint_id,
                date_key,
                source,
                json.dumps(hourly, ensure_ascii=False),
                json.dumps(astronomy, ensure_ascii=False) if astronomy else None,
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_meteo_day_cache(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    source: str = "historical_forecast",
) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT hourly_json, astronomy_json FROM meteo_day_cache
            WHERE spot_id=? AND viewpoint_id=? AND date_key=? AND source=?
            """,
            (spot_id, viewpoint_id, date_key, source),
        ).fetchone()
    if not row:
        return None
    try:
        hourly = json.loads(row["hourly_json"])
        astronomy = json.loads(row["astronomy_json"]) if row["astronomy_json"] else None
    except json.JSONDecodeError:
        return None
    return {"hourly": hourly, "astronomy": astronomy}


def load_full_day_meteo_rows(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
) -> list[dict[str, Any]]:
    """优先 meteo_day_cache，否则聚合 meteo_hourly 逐时行。"""
    bundle = load_meteo_day_cache(spot_id, viewpoint_id, date_key)
    if bundle and bundle.get("hourly"):
        from app.services.meteo_cache import hour_rows_from_hourly

        return hour_rows_from_hourly(bundle["hourly"], date_key)

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT raw_json FROM meteo_hourly
            WHERE spot_id=? AND viewpoint_id=? AND ts LIKE ?
            ORDER BY ts
            """,
            (spot_id, viewpoint_id, f"{date_key}T%"),
        ).fetchall()
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            parsed.append(raw)
    return parsed


def save_meteo_hourly_batch(
    *,
    spot_id: Optional[str],
    viewpoint_id: Optional[str],
    lat: float,
    lng: float,
    elevation: Optional[float],
    rows: list[dict[str, Any]],
    source: str,
) -> None:
    for row in rows:
        ts = str(row.get("time") or "")
        if not ts:
            continue
        save_meteo_hourly(
            spot_id=spot_id,
            viewpoint_id=viewpoint_id,
            lat=lat,
            lng=lng,
            elevation=elevation,
            ts=ts,
            source=source,
            raw=row,
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


def default_forecast_issue_time(target_date: str) -> str:
    """标注日 D 的 operational issue：D-1 18:00 北京时间。"""
    from datetime import date as date_cls, datetime, timedelta

    d = date_cls.fromisoformat(target_date)
    issued = d - timedelta(days=1)
    dt = datetime(issued.year, issued.month, issued.day, 18, 0, tzinfo=TZ)
    return dt.isoformat()


def save_forecast_archive_rows(
    *,
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    issue_time: str,
    rows: list[dict[str, Any]],
    source: str = "historical_forecast",
    db_path: Path | None = None,
) -> int:
    from datetime import datetime

    issue_dt = datetime.fromisoformat(issue_time)
    path = db_path or Path(settings.cloudsea_db_path)
    saved = 0
    conn = sqlite3.connect(path)
    try:
        for row in rows:
            ts = str(row.get("time") or "")
            if not ts:
                continue
            valid_dt = datetime.fromisoformat(ts)
            if valid_dt.tzinfo is None:
                valid_dt = valid_dt.replace(tzinfo=TZ)
            lead_hours = (valid_dt - issue_dt).total_seconds() / 3600.0
            conn.execute(
                """
                INSERT INTO meteo_forecast_archive
                (spot_id, viewpoint_id, target_date, issue_time, ts, lead_hours, source, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spot_id, viewpoint_id, target_date, issue_time, ts) DO UPDATE SET
                    lead_hours=excluded.lead_hours,
                    source=excluded.source,
                    raw_json=excluded.raw_json
                """,
                (
                    spot_id,
                    viewpoint_id,
                    target_date,
                    issue_time,
                    ts,
                    lead_hours,
                    source,
                    json.dumps(row, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            saved += 1
        conn.commit()
    finally:
        conn.close()
    return saved


def load_forecast_archive_precursor(
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    *,
    issue_time: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    from app.services.meteo_backfill import precursor_hour_keys

    issue = issue_time or default_forecast_issue_time(target_date)
    keys = set(precursor_hour_keys(target_date))
    path = db_path or Path(settings.cloudsea_db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT raw_json FROM meteo_forecast_archive
            WHERE spot_id=? AND viewpoint_id=? AND target_date=? AND issue_time=?
            ORDER BY ts
            """,
            (spot_id, viewpoint_id, target_date, issue),
        ).fetchall()
    finally:
        conn.close()
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict) and str(raw.get("time") or "") in keys:
            parsed.append(raw)
    return sorted(parsed, key=lambda r: str(r.get("time")))


def forecast_archive_precursor_complete(
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    *,
    issue_time: str | None = None,
    db_path: Path | None = None,
) -> bool:
    from app.engine.cloudsea_features import meteo_row_complete
    from app.services.meteo_backfill import precursor_hour_keys

    issue = issue_time or default_forecast_issue_time(target_date)
    rows = load_forecast_archive_precursor(
        spot_id, viewpoint_id, target_date, issue_time=issue, db_path=db_path
    )
    have = {str(r.get("time")) for r in rows if meteo_row_complete(r)}
    return all(k in have for k in precursor_hour_keys(target_date))


def insert_prediction_access_log(
    *,
    created_at: str,
    target_date: str,
    lead_hours_to_dawn: float | None,
    spot_id: str | None,
    viewpoint_id: str | None,
    location_id: str | None,
    lat: float,
    lng: float,
    elevation: float | None,
    page_source: str | None,
    client_id: str | None,
    model_version: str | None,
    data_source: str,
    prediction: dict[str, Any],
    meteo_snapshot: dict[str, Any],
    feature_snapshot: dict[str, Any] | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO prediction_access_log
            (created_at, target_date, lead_hours_to_dawn, spot_id, viewpoint_id,
             location_id, lat, lng, elevation, page_source, client_id, model_version,
             data_source, prediction_json, meteo_snapshot_json, feature_snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                target_date,
                lead_hours_to_dawn,
                spot_id,
                viewpoint_id,
                location_id,
                lat,
                lng,
                elevation,
                page_source,
                client_id,
                model_version,
                data_source,
                json.dumps(prediction, ensure_ascii=False),
                json.dumps(meteo_snapshot, ensure_ascii=False),
                json.dumps(feature_snapshot, ensure_ascii=False) if feature_snapshot else None,
            ),
        )
        return int(cur.lastrowid)


def get_latest_prediction_access_log(
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM prediction_access_log
            WHERE spot_id=? AND viewpoint_id=? AND target_date=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (spot_id, viewpoint_id, target_date),
        ).fetchone()
    if not row:
        return None
    return _parse_access_log_row(row)


def touch_prediction_access_log(
    log_id: int,
    *,
    created_at: str,
    lead_hours_to_dawn: float | None,
    prediction: dict[str, Any],
    page_source: str | None = None,
    client_id: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE prediction_access_log SET
                created_at=?,
                lead_hours_to_dawn=?,
                prediction_json=?,
                page_source=COALESCE(?, page_source),
                client_id=COALESCE(?, client_id)
            WHERE id=?
            """,
            (
                created_at,
                lead_hours_to_dawn,
                json.dumps(prediction, ensure_ascii=False),
                page_source,
                client_id,
                log_id,
            ),
        )


def _parse_access_log_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("prediction_json", "meteo_snapshot_json", "feature_snapshot_json"):
        raw = out.pop(key, None)
        parsed_key = key.replace("_json", "")
        if raw:
            try:
                out[parsed_key] = json.loads(raw)
            except json.JSONDecodeError:
                out[parsed_key] = None
        else:
            out[parsed_key] = None
    return out


def get_prediction_access_log(access_log_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT l.*,
                   o.reconciled_at, o.label_status, o.label_id,
                   o.actual_meteo_json, o.forecast_error_json,
                   o.predicted_positive, o.label_positive, o.direction_ok,
                   o.diagnosis_json
            FROM prediction_access_log l
            LEFT JOIN prediction_access_outcome o ON o.access_log_id = l.id
            WHERE l.id=?
            """,
            (access_log_id,),
        ).fetchone()
    if not row:
        return None
    out = _parse_access_log_row(row)
    for key in ("actual_meteo_json", "forecast_error_json", "diagnosis_json"):
        raw = out.pop(key, None)
        parsed_key = key.replace("_json", "")
        if raw:
            try:
                out[parsed_key] = json.loads(raw)
            except json.JSONDecodeError:
                out[parsed_key] = None
    return out


def list_prediction_access_logs(
    *,
    spot_id: str,
    viewpoint_id: str,
    target_date: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = """
        SELECT l.*,
               o.reconciled_at, o.label_status, o.label_id,
               o.predicted_positive, o.label_positive, o.direction_ok,
               o.diagnosis_json
        FROM prediction_access_log l
        LEFT JOIN prediction_access_outcome o ON o.access_log_id = l.id
        WHERE l.spot_id=? AND l.viewpoint_id=?
    """
    params: list[Any] = [spot_id, viewpoint_id]
    if target_date:
        sql += " AND l.target_date=?"
        params.append(target_date)
    sql += " ORDER BY l.created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = _parse_access_log_row(row)
        diag_raw = item.pop("diagnosis_json", None)
        if diag_raw:
            try:
                item["diagnosis"] = json.loads(diag_raw)
            except json.JSONDecodeError:
                item["diagnosis"] = None
        out.append(item)
    return out


def list_unreconciled_access_logs(
    *,
    target_date: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    sql = """
        SELECT l.id, l.target_date, l.spot_id, l.viewpoint_id
        FROM prediction_access_log l
        LEFT JOIN prediction_access_outcome o ON o.access_log_id = l.id
        WHERE o.access_log_id IS NULL
    """
    params: list[Any] = []
    if target_date:
        sql += " AND l.target_date=?"
        params.append(target_date)
    sql += " ORDER BY l.target_date, l.id LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_access_log_ids_for_date(target_date: str) -> list[int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM prediction_access_log WHERE target_date=? ORDER BY id",
            (target_date,),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def upsert_prediction_access_outcome(
    *,
    access_log_id: int,
    label_status: str | None,
    label_id: int | None,
    actual_meteo: dict[str, Any],
    forecast_error: dict[str, Any],
    predicted_positive: int,
    label_positive: int,
    direction_ok: int | None,
    diagnosis: dict[str, Any],
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO prediction_access_outcome
            (access_log_id, reconciled_at, label_status, label_id,
             actual_meteo_json, forecast_error_json,
             predicted_positive, label_positive, direction_ok, diagnosis_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(access_log_id) DO UPDATE SET
                reconciled_at=excluded.reconciled_at,
                label_status=excluded.label_status,
                label_id=excluded.label_id,
                actual_meteo_json=excluded.actual_meteo_json,
                forecast_error_json=excluded.forecast_error_json,
                predicted_positive=excluded.predicted_positive,
                label_positive=excluded.label_positive,
                direction_ok=excluded.direction_ok,
                diagnosis_json=excluded.diagnosis_json
            """,
            (
                access_log_id,
                _now_iso(),
                label_status,
                label_id,
                json.dumps(actual_meteo, ensure_ascii=False),
                json.dumps(forecast_error, ensure_ascii=False),
                predicted_positive,
                label_positive,
                direction_ok,
                json.dumps(diagnosis, ensure_ascii=False),
            ),
        )


def export_prediction_feedback(
    *,
    spot_id: str | None = None,
    viewpoint_id: str | None = None,
    month: str | None = None,
    export_format: str = "json",
) -> Any:
    sql = """
        SELECT l.id, l.created_at, l.target_date, l.lead_hours_to_dawn,
               l.spot_id, l.viewpoint_id, l.lat, l.lng, l.elevation,
               l.page_source, l.client_id, l.model_version, l.data_source,
               l.prediction_json, l.meteo_snapshot_json,
               o.label_status, o.predicted_positive, o.label_positive,
               o.direction_ok, o.diagnosis_json
        FROM prediction_access_log l
        LEFT JOIN prediction_access_outcome o ON o.access_log_id = l.id
        WHERE 1=1
    """
    params: list[Any] = []
    if spot_id:
        sql += " AND l.spot_id=?"
        params.append(spot_id)
    if viewpoint_id:
        sql += " AND l.viewpoint_id=?"
        params.append(viewpoint_id)
    if month:
        sql += " AND l.target_date LIKE ?"
        params.append(f"{month}-%")
    sql += " ORDER BY l.target_date, l.created_at"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            pred = json.loads(item.pop("prediction_json") or "{}")
        except json.JSONDecodeError:
            pred = {}
        try:
            diag = json.loads(item.pop("diagnosis_json") or "null")
        except json.JSONDecodeError:
            diag = None
        item.pop("meteo_snapshot_json", None)
        item["peak_cloudsea_prob"] = pred.get("peak_cloudsea_prob")
        item["diagnosis"] = diag
        records.append(item)

    if export_format == "csv":
        import csv
        import io

        buf = io.StringIO()
        if not records:
            return {"content_type": "text/csv", "body": ""}
        fieldnames = list(records[0].keys())
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            flat = dict(rec)
            if flat.get("diagnosis"):
                flat["diagnosis"] = json.dumps(flat["diagnosis"], ensure_ascii=False)
            writer.writerow(flat)
        return {"content_type": "text/csv", "body": buf.getvalue()}

    return {"count": len(records), "records": records}

