from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ip TEXT,
    referer TEXT,
    channel TEXT,
    browser TEXT,
    os TEXT,
    device TEXT,
    path TEXT,
    method TEXT,
    status_code INTEGER,
    duration_ms INTEGER,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_channel ON events(channel);
CREATE INDEX IF NOT EXISTS idx_events_ip ON events(ip);
"""


def _db_path() -> Path:
    path = Path(settings.analytics_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def init_store() -> None:
    with _lock:
        _get_conn()
    purge_expired()


def purge_expired() -> int:
    days = max(settings.analytics_retention_days, 1)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _lock:
        cur = _get_conn().execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        _get_conn().commit()
        return cur.rowcount


def insert_event(
    *,
    event_type: str,
    ip: str = "",
    referer: str = "",
    channel: str = "",
    browser: str = "",
    os: str = "",
    device: str = "",
    path: str = "",
    method: str = "",
    status_code: int | None = None,
    duration_ms: int | None = None,
    payload: dict[str, Any] | None = None,
    ts: str | None = None,
) -> None:
    if not settings.analytics_enabled:
        return
    row_ts = ts or datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    with _lock:
        _get_conn().execute(
            """
            INSERT INTO events (
                ts, event_type, ip, referer, channel, browser, os, device,
                path, method, status_code, duration_ms, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_ts,
                event_type,
                ip,
                referer,
                channel,
                browser,
                os,
                device,
                path,
                method,
                status_code,
                duration_ms,
                payload_json,
            ),
        )
        _get_conn().commit()


def _range_clause(from_ts: str | None, to_ts: str | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if from_ts:
        clauses.append("ts >= ?")
        params.append(from_ts)
    if to_ts:
        clauses.append("ts <= ?")
        params.append(to_ts)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _and(where: str, clause: str) -> str:
    return f"{where} AND {clause}" if where else f"WHERE {clause}"


def query_summary(from_ts: str | None = None, to_ts: str | None = None) -> dict[str, Any]:
    where, params = _range_clause(from_ts, to_ts)
    uv_where = _and(where, "ip != '' AND ip != 'unknown'")
    api_where = _and(where, "event_type LIKE 'api_%'")
    visit_where = _and(where, "event_type = 'page_visit'")
    search_where = _and(
        where,
        "event_type IN ('search', 'poi_search', 'api_search') "
        "AND json_extract(payload_json, '$.keyword') IS NOT NULL "
        "AND json_extract(payload_json, '$.keyword') != ''",
    )
    with _lock:
        conn = _get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM events {where}", params).fetchone()[0]
        uv = conn.execute(f"SELECT COUNT(DISTINCT ip) FROM events {uv_where}", params).fetchone()[0]
        api_calls = conn.execute(f"SELECT COUNT(*) FROM events {api_where}", params).fetchone()[0]
        visits = conn.execute(f"SELECT COUNT(*) FROM events {visit_where}", params).fetchone()[0]
        top_search = conn.execute(
            f"""
            SELECT json_extract(payload_json, '$.keyword') AS kw, COUNT(*) AS c
            FROM events {search_where}
            GROUP BY kw ORDER BY c DESC LIMIT 10
            """,
            params,
        ).fetchall()
    return {
        "total_events": total,
        "unique_ips": uv,
        "api_calls": api_calls,
        "page_visits": visits,
        "top_searches": [{"keyword": r[0], "count": r[1]} for r in top_search],
    }


def query_channels(from_ts: str | None = None, to_ts: str | None = None) -> list[dict[str, Any]]:
    where, params = _range_clause(from_ts, to_ts)
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT channel, COUNT(*) AS c FROM events {where}
            GROUP BY channel ORDER BY c DESC
            """,
            params,
        ).fetchall()
    return [{"channel": r[0] or "unknown", "count": r[1]} for r in rows]


def query_spots(from_ts: str | None = None, to_ts: str | None = None) -> list[dict[str, Any]]:
    where, params = _range_clause(from_ts, to_ts)
    spot_where = _and(
        where,
        "event_type IN ('spot_select', 'viewpoint_select', 'api_predict_viewpoint', 'api_predict')",
    )
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT
              COALESCE(json_extract(payload_json, '$.spot_id'), json_extract(payload_json, '$.spotId')) AS sid,
              COALESCE(json_extract(payload_json, '$.spot_name'), json_extract(payload_json, '$.name')) AS sname,
              COUNT(*) AS c
            FROM events {spot_where}
            GROUP BY sid, sname HAVING sid IS NOT NULL ORDER BY c DESC LIMIT 30
            """,
            params,
        ).fetchall()
    return [{"spot_id": r[0], "spot_name": r[1], "count": r[2]} for r in rows]


def query_searches(from_ts: str | None = None, to_ts: str | None = None) -> dict[str, Any]:
    where, params = _range_clause(from_ts, to_ts)
    search_where = _and(where, "event_type IN ('search', 'poi_search', 'api_search')")
    top_where = _and(
        where,
        "event_type IN ('search', 'poi_search', 'api_search') "
        "AND json_extract(payload_json, '$.keyword') IS NOT NULL "
        "AND json_extract(payload_json, '$.keyword') != ''",
    )
    with _lock:
        top = _get_conn().execute(
            f"""
            SELECT json_extract(payload_json, '$.keyword') AS kw, COUNT(*) AS c
            FROM events {top_where}
            GROUP BY kw ORDER BY c DESC LIMIT 50
            """,
            params,
        ).fetchall()
        recent = _get_conn().execute(
            f"""
            SELECT ts, event_type, ip, json_extract(payload_json, '$.keyword') AS kw,
                   json_extract(payload_json, '$.result_count') AS rc
            FROM events {search_where}
            ORDER BY ts DESC LIMIT 100
            """,
            params,
        ).fetchall()
    return {
        "top": [{"keyword": r[0], "count": r[1]} for r in top],
        "recent": [
            {
                "ts": r[0],
                "event_type": r[1],
                "ip": r[2],
                "keyword": r[3],
                "result_count": r[4],
            }
            for r in recent
        ],
    }


def query_clients(from_ts: str | None = None, to_ts: str | None = None) -> dict[str, Any]:
    where, params = _range_clause(from_ts, to_ts)
    with _lock:
        conn = _get_conn()
        browsers = conn.execute(
            f"SELECT browser, COUNT(*) FROM events {where} GROUP BY browser ORDER BY 2 DESC",
            params,
        ).fetchall()
        systems = conn.execute(
            f"SELECT os, COUNT(*) FROM events {where} GROUP BY os ORDER BY 2 DESC",
            params,
        ).fetchall()
        devices = conn.execute(
            f"SELECT device, COUNT(*) FROM events {where} GROUP BY device ORDER BY 2 DESC",
            params,
        ).fetchall()
        top_ips = conn.execute(
            f"""
            SELECT ip, COUNT(*) AS c FROM events {_and(where, "ip != '' AND ip != 'unknown'")}
            GROUP BY ip ORDER BY c DESC LIMIT 50
            """,
            params,
        ).fetchall()
    return {
        "browsers": [{"name": r[0], "count": r[1]} for r in browsers],
        "os": [{"name": r[0], "count": r[1]} for r in systems],
        "devices": [{"name": r[0], "count": r[1]} for r in devices],
        "top_ips": [{"ip": r[0], "count": r[1]} for r in top_ips],
    }


def query_api_stats(from_ts: str | None = None, to_ts: str | None = None) -> list[dict[str, Any]]:
    where, params = _range_clause(from_ts, to_ts)
    api_where = _and(where, "event_type LIKE 'api_%' AND path IS NOT NULL AND path != ''")
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT path,
                   COUNT(*) AS calls,
                   AVG(duration_ms) AS avg_ms,
                   SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS errors
            FROM events {api_where}
            GROUP BY path ORDER BY calls DESC
            """,
            params,
        ).fetchall()
    return [
        {
            "path": r[0],
            "calls": r[1],
            "avg_ms": round(r[2] or 0, 1),
            "errors": r[3],
        }
        for r in rows
    ]


def query_events(limit: int = 100, from_ts: str | None = None, to_ts: str | None = None) -> list[dict[str, Any]]:
    where, params = _range_clause(from_ts, to_ts)
    params.append(min(max(limit, 1), 500))
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT id, ts, event_type, ip, channel, browser, os, device, path, method,
                   status_code, duration_ms, referer, payload_json
            FROM events {where}
            ORDER BY ts DESC LIMIT ?
            """,
            params,
        ).fetchall()
    result = []
    for r in rows:
        result.append(
            {
                "id": r[0],
                "ts": r[1],
                "event_type": r[2],
                "ip": r[3],
                "channel": r[4],
                "browser": r[5],
                "os": r[6],
                "device": r[7],
                "path": r[8],
                "method": r[9],
                "status_code": r[10],
                "duration_ms": r[11],
                "referer": r[12],
                "payload": json.loads(r[13] or "{}"),
            }
        )
    return result


def export_csv(from_ts: str | None = None, to_ts: str | None = None, limit: int = 5000) -> str:
    events = query_events(limit=limit, from_ts=from_ts, to_ts=to_ts)
    lines = ["ts,event_type,ip,channel,browser,os,device,path,method,status_code,duration_ms,payload"]
    for e in events:
        payload = json.dumps(e.get("payload") or {}, ensure_ascii=False).replace('"', '""')
        lines.append(
            f"{e['ts']},{e['event_type']},{e['ip']},{e['channel']},{e['browser']},"
            f"{e['os']},{e['device']},{e['path'] or ''},{e['method'] or ''},"
            f"{e['status_code'] or ''},{e['duration_ms'] or ''},\"{payload}\""
        )
    return "\n".join(lines)
