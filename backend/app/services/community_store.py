from __future__ import annotations

import math
import secrets
import sqlite3
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.cloudsea_store import _connect, _now_iso

TZ = ZoneInfo("Asia/Shanghai")
COMMUNITY_SPOT_ID = "community"


def _today_shanghai() -> str:
    return datetime.now(TZ).date().isoformat()


def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_community_schema() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS contributors (
                id TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                label_count_total INTEGER NOT NULL DEFAULT 0,
                label_count_approved INTEGER NOT NULL DEFAULT 0,
                trust_level TEXT NOT NULL DEFAULT 'new',
                blocked INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS community_locations (
                id TEXT PRIMARY KEY,
                slug TEXT UNIQUE,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                elevation REAL,
                contributor_id TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'poi',
                status TEXT NOT NULL DEFAULT 'active',
                review_status TEXT NOT NULL DEFAULT 'pending',
                label_count INTEGER NOT NULL DEFAULT 0,
                approved_label_count INTEGER NOT NULL DEFAULT 0,
                curated_spot_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_community_loc_contrib
                ON community_locations(contributor_id);

            CREATE TABLE IF NOT EXISTS contributor_daily_quota (
                contributor_id TEXT NOT NULL,
                quota_date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (contributor_id, quota_date)
            );
            """
        )
        _add_column(conn, "cloudsea_labels", "location_id", "TEXT")
        _add_column(conn, "cloudsea_labels", "lat", "REAL")
        _add_column(conn, "cloudsea_labels", "lng", "REAL")
        _add_column(conn, "cloudsea_labels", "location_name", "TEXT")
        _add_column(conn, "cloudsea_labels", "contributor_id", "TEXT")
        _add_column(conn, "cloudsea_labels", "review_status", "TEXT DEFAULT 'approved'")
        _add_column(conn, "cloudsea_labels", "reviewed_at", "TEXT")
        _add_column(conn, "cloudsea_labels", "reviewed_by", "TEXT")
        conn.execute(
            "UPDATE cloudsea_labels SET review_status='approved' WHERE review_status IS NULL"
        )
        conn.execute(
            "UPDATE cloudsea_labels SET contributor_id='seed' WHERE labeled_by='seed'"
        )
        _migrate_community_auto_approve(conn)
    _run_pending_auto_curate()


def _migrate_community_auto_approve(conn: sqlite3.Connection) -> None:
    if not settings.cloudsea_community_auto_approve:
        return
    now = _now_iso()
    conn.execute(
        """
        UPDATE cloudsea_labels
        SET review_status='approved', reviewed_at=?, reviewed_by='auto'
        WHERE spot_id=? AND review_status='pending'
        """,
        (now, COMMUNITY_SPOT_ID),
    )
    conn.execute(
        """
        UPDATE community_locations
        SET label_count = (
                SELECT COUNT(*) FROM cloudsea_labels
                WHERE location_id = community_locations.id
            ),
            approved_label_count = (
                SELECT COUNT(*) FROM cloudsea_labels
                WHERE location_id = community_locations.id
                  AND review_status='approved'
            ),
            updated_at=?
        WHERE status='active'
        """,
        (now,),
    )
    conn.execute(
        """
        UPDATE contributors
        SET label_count_approved = (
                SELECT COUNT(*) FROM cloudsea_labels
                WHERE contributor_id = contributors.id
                  AND review_status='approved'
            )
        """
    )
    from app.config import curated_spots_dir

    curated_dir = curated_spots_dir()
    for row in conn.execute(
        """
        SELECT id, curated_spot_id FROM community_locations
        WHERE status='active' AND curated_spot_id IS NOT NULL
        """
    ).fetchall():
        if not (curated_dir / f"{row['curated_spot_id']}.json").exists():
            conn.execute(
                "UPDATE community_locations SET curated_spot_id=NULL WHERE id=?",
                (row["id"],),
            )


def _run_pending_auto_curate() -> None:
    if not settings.cloudsea_community_auto_approve:
        return
    from app.config import curated_spots_dir
    from app.services.curate_service import curate_community_location
    from app.services.spot_loader import reload_spots

    with _connect() as conn:
        location_ids = [
            row["id"]
            for row in conn.execute(
                """
                SELECT id FROM community_locations
                WHERE status='active' AND approved_label_count >= ?
                """,
                (settings.cloudsea_curate_min_labels,),
            ).fetchall()
        ]
    for location_id in location_ids:
        try:
            curate_community_location(location_id)
        except ValueError:
            pass

    with _connect() as conn:
        known_ids = {
            row["curated_spot_id"]
            for row in conn.execute(
                "SELECT curated_spot_id FROM community_locations WHERE curated_spot_id IS NOT NULL"
            ).fetchall()
            if row["curated_spot_id"]
        }
    curated_dir = curated_spots_dir()
    for file in curated_dir.glob("*.json"):
        if file.stem not in known_ids:
            file.unlink(missing_ok=True)
    reload_spots()


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def validate_contributor_id(contributor_id: str) -> None:
    if not contributor_id or not contributor_id.startswith("cid_") or len(contributor_id) > 64:
        raise ValueError("无效的 Contributor ID")


def touch_contributor(contributor_id: str) -> dict[str, Any]:
    validate_contributor_id(contributor_id)
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM contributors WHERE id=?", (contributor_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE contributors SET last_seen_at=? WHERE id=?",
                (now, contributor_id),
            )
            return dict(row)
        conn.execute(
            """
            INSERT INTO contributors
            (id, first_seen_at, last_seen_at, label_count_total, label_count_approved, trust_level, blocked)
            VALUES (?, ?, ?, 0, 0, 'new', 0)
            """,
            (contributor_id, now, now),
        )
    return get_contributor(contributor_id) or {}


def get_contributor(contributor_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM contributors WHERE id=?", (contributor_id,)).fetchone()
    return dict(row) if row else None


def assert_contributor_active(contributor_id: str) -> dict[str, Any]:
    contrib = touch_contributor(contributor_id)
    if contrib.get("blocked"):
        raise PermissionError("该贡献者已被限制标注")
    return contrib


def _trust_level(approved: int) -> str:
    if approved >= 30:
        return "trusted"
    if approved >= 10:
        return "regular"
    return "new"


def _update_trust_level(conn: sqlite3.Connection, contributor_id: str) -> None:
    row = conn.execute(
        "SELECT label_count_approved FROM contributors WHERE id=?", (contributor_id,)
    ).fetchone()
    if not row:
        return
    level = _trust_level(int(row["label_count_approved"]))
    conn.execute(
        "UPDATE contributors SET trust_level=? WHERE id=?",
        (level, contributor_id),
    )


def count_new_labels_today(contributor_id: str) -> int:
    today = _today_shanghai()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT count FROM contributor_daily_quota
            WHERE contributor_id=? AND quota_date=?
            """,
            (contributor_id, today),
        ).fetchone()
    return int(row["count"]) if row else 0


def increment_daily_quota(conn: sqlite3.Connection, contributor_id: str) -> None:
    today = _today_shanghai()
    conn.execute(
        """
        INSERT INTO contributor_daily_quota (contributor_id, quota_date, count)
        VALUES (?, ?, 1)
        ON CONFLICT(contributor_id, quota_date)
        DO UPDATE SET count = count + 1
        """,
        (contributor_id, today),
    )


def check_daily_quota(contributor_id: str) -> None:
    cap = settings.cloudsea_daily_label_cap
    used = count_new_labels_today(contributor_id)
    if used >= cap:
        raise PermissionError(f"今日标注已达上限 {cap} 条")


def label_exists(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    window_start: int = 3,
    window_end: int = 7,
) -> bool:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=? AND date=?
              AND window_start=? AND window_end=?
            """,
            (spot_id, viewpoint_id, date_key, window_start, window_end),
        ).fetchone()
    return row is not None


def count_community_locations(contributor_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM community_locations
            WHERE contributor_id=? AND status='active'
            """,
            (contributor_id,),
        ).fetchone()
    return int(row["c"]) if row else 0


def find_nearby_location(lat: float, lng: float, radius_m: Optional[float] = None) -> Optional[dict[str, Any]]:
    radius = radius_m or settings.cloudsea_dedup_radius_m
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM community_locations WHERE status='active'"
        ).fetchall()
    best: Optional[dict[str, Any]] = None
    best_d = radius + 1
    for row in rows:
        d = haversine_m(lat, lng, row["lat"], row["lng"])
        if d <= radius and d < best_d:
            best = dict(row)
            best_d = d
    return best


def _new_location_id() -> str:
    return f"cs_{secrets.token_hex(4)}"


def create_community_location(
    *,
    contributor_id: str,
    name: str,
    lat: float,
    lng: float,
    elevation: Optional[float] = None,
    source: str = "poi",
) -> dict[str, Any]:
    assert_contributor_active(contributor_id)
    nearby = find_nearby_location(lat, lng)
    if nearby:
        return nearby
    if count_community_locations(contributor_id) >= settings.cloudsea_max_locations_per_contributor:
        raise PermissionError(
            f"最多注册 {settings.cloudsea_max_locations_per_contributor} 个社区点位"
        )
    loc_id = _new_location_id()
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO community_locations
            (id, slug, name, lat, lng, elevation, contributor_id, source, status,
             review_status, label_count, approved_label_count, curated_spot_id, created_at, updated_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'active', 'pending', 0, 0, NULL, ?, ?)
            """,
            (loc_id, name, lat, lng, elevation, contributor_id, source, now, now),
        )
        row = conn.execute(
            "SELECT * FROM community_locations WHERE id=?", (loc_id,)
        ).fetchone()
    return dict(row) if row else {}


def get_community_location(location_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM community_locations WHERE id=? AND status='active'",
            (location_id,),
        ).fetchone()
    return dict(row) if row else None


def get_community_location_by_curated_spot(spot_id: str) -> Optional[dict[str, Any]]:
    direct = get_community_location(spot_id)
    if direct:
        return direct
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM community_locations
            WHERE status='active' AND curated_spot_id=?
            """,
            (spot_id,),
        ).fetchone()
    return dict(row) if row else None


def list_community_locations(contributor_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM community_locations
            WHERE contributor_id=? AND status='active'
            ORDER BY updated_at DESC
            """,
            (contributor_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_or_create_location(
    *,
    contributor_id: str,
    location_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    name: Optional[str] = None,
    elevation: Optional[float] = None,
    source: str = "poi",
) -> dict[str, Any]:
    if location_id:
        loc = get_community_location(location_id)
        if not loc:
            raise ValueError("社区点位未找到")
        return loc
    if lat is None or lng is None:
        raise ValueError("需提供 location_id 或 lat/lng")
    nearby = find_nearby_location(lat, lng)
    if nearby:
        return nearby
    return create_community_location(
        contributor_id=contributor_id,
        name=name or "自定义位置",
        lat=lat,
        lng=lng,
        elevation=elevation,
        source=source,
    )


def community_label_keys(location_id: str) -> tuple[str, str]:
    return COMMUNITY_SPOT_ID, location_id


def get_contributor_stats(contributor_id: str) -> dict[str, Any]:
    assert_contributor_active(contributor_id)
    today = _today_shanghai()
    with _connect() as conn:
        totals = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN review_status='approved' THEN 1 ELSE 0 END) AS approved,
              SUM(CASE WHEN review_status='pending' THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN review_status='rejected' THEN 1 ELSE 0 END) AS rejected
            FROM cloudsea_labels WHERE contributor_id=?
            """,
            (contributor_id,),
        ).fetchone()
        used_today = count_new_labels_today(contributor_id)
    return {
        "contributor_id": contributor_id,
        "labels_total": int(totals["total"] or 0),
        "labels_approved": int(totals["approved"] or 0),
        "labels_pending": int(totals["pending"] or 0),
        "labels_rejected": int(totals["rejected"] or 0),
        "labels_today": used_today,
        "daily_cap": settings.cloudsea_daily_label_cap,
        "locations_count": count_community_locations(contributor_id),
        "locations_cap": settings.cloudsea_max_locations_per_contributor,
        "quota_date": today,
    }


def increment_location_label_count(
    conn: sqlite3.Connection,
    location_id: str,
    *,
    approved_delta: int = 0,
    label_delta: int = 1,
) -> None:
    conn.execute(
        """
        UPDATE community_locations
        SET label_count = label_count + ?,
            approved_label_count = approved_label_count + ?,
            updated_at = ?
        WHERE id = ?
        """,
        (label_delta, approved_delta, _now_iso(), location_id),
    )


def list_review_queue(limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT l.*, cl.name AS community_name
            FROM cloudsea_labels l
            LEFT JOIN community_locations cl ON cl.id = l.location_id
            WHERE l.review_status = 'pending'
            ORDER BY l.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def review_label(
    label_id: int,
    *,
    review_status: str,
    reviewed_by: str = "admin",
) -> Optional[dict[str, Any]]:
    if review_status not in ("approved", "rejected"):
        raise ValueError("review_status 须为 approved 或 rejected")
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM cloudsea_labels WHERE id=?", (label_id,)).fetchone()
        if not row:
            return None
        old = dict(row)
        if old.get("review_status") == review_status:
            return old
        conn.execute(
            """
            UPDATE cloudsea_labels
            SET review_status=?, reviewed_at=?, reviewed_by=?, updated_at=?
            WHERE id=?
            """,
            (review_status, now, reviewed_by, now, label_id),
        )
        contributor_id = old.get("contributor_id")
        if contributor_id and review_status == "approved" and old.get("review_status") != "approved":
            conn.execute(
                """
                UPDATE contributors
                SET label_count_approved = label_count_approved + 1
                WHERE id=?
                """,
                (contributor_id,),
            )
            _update_trust_level(conn, contributor_id)
            location_id = old.get("location_id")
            if location_id:
                conn.execute(
                    """
                    UPDATE community_locations
                    SET approved_label_count = approved_label_count + 1, updated_at=?
                    WHERE id=?
                    """,
                    (now, location_id),
                )
        updated = conn.execute("SELECT * FROM cloudsea_labels WHERE id=?", (label_id,)).fetchone()
    return dict(updated) if updated else None


def calendar_summary_extended(
    spot_id: str,
    viewpoint_id: str,
    month: str,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT date, status, review_status FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=? AND date LIKE ?
            ORDER BY date
            """,
            (spot_id, viewpoint_id, f"{month}-%"),
        ).fetchall()
    return [
        {"date": r["date"], "status": r["status"], "review_status": r["review_status"]}
        for r in rows
    ]


def assert_label_date_allowed(date_key: str) -> None:
    target = date.fromisoformat(date_key)
    if target > datetime.now(TZ).date():
        raise ValueError("不能标注未来日期")
