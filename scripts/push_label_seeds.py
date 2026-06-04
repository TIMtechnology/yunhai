#!/usr/bin/env python3
"""将 label-seeds JSON 推送到生产（或本地）cloudsea 标注库。"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def push_via_api(base: str, token: str, labels: list[dict]) -> tuple[int, int]:
    ok = err = 0
    for row in labels:
        body = json.dumps(
            {
                "spot_id": row["spot_id"],
                "viewpoint_id": row["viewpoint_id"],
                "date": row["date"],
                "status": row["status"],
                "notes": row.get("notes", ""),
                "confidence": "confirmed",
            }
        ).encode()
        req = urllib.request.Request(
            f"{base.rstrip('/')}/api/internal/cloudsea/labels",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Cloudsea-Token": token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            ok += 1
            print(f"  OK {row['spot_id']}/{row['viewpoint_id']} {row['date']} {row['status']}")
        except urllib.error.HTTPError as exc:
            err += 1
            detail = exc.read().decode()[:200]
            print(f"  ERR {row['date']}: HTTP {exc.code} {detail}")
    return ok, err


def push_via_db(db_path: Path, labels: list[dict]) -> int:
    from app.services.cloudsea_store import init_store, upsert_label

    os.environ["CLOUDSEA_DB_PATH"] = str(db_path.resolve())
    init_store()
    n = 0
    for row in labels:
        upsert_label(
            spot_id=row["spot_id"],
            viewpoint_id=row["viewpoint_id"],
            date_key=row["date"],
            status=row["status"],
            notes=row.get("notes", ""),
            labeled_by="admin_batch",
            review_status="approved",
        )
        n += 1
        print(f"  OK {row['spot_id']}/{row['viewpoint_id']} {row['date']} {row['status']}")
    return n


def count_labels(db_path: Path, spot_id: str, viewpoint_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT status, COUNT(*) c FROM cloudsea_labels WHERE spot_id=? AND viewpoint_id=? GROUP BY status",
        (spot_id, viewpoint_id),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) c FROM cloudsea_labels WHERE spot_id=? AND viewpoint_id=?",
        (spot_id, viewpoint_id),
    ).fetchone()["c"]
    conn.close()
    by = {r["status"]: r["c"] for r in rows}
    return {"total": total, **by}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds",
        default=str(ROOT / "data" / "cloudsea" / "label-seeds-batch2.json"),
    )
    parser.add_argument(
        "--api-base",
        default=os.environ.get("CLOUDSEA_API_BASE", "https://yunhai.timkj.com"),
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("CLOUDSEA_ADMIN_TOKEN", ""),
    )
    parser.add_argument(
        "--db",
        help="若指定则直接写 SQLite（本地/拷贝的 prod.db），否则走 API",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.seeds).read_text(encoding="utf-8"))
    labels = payload["labels"]
    print(f"推送 {len(labels)} 条标注 …")

    if args.db:
        n = push_via_db(Path(args.db), labels)
        print(f"完成: {n} 条写入 {args.db}")
        db = Path(args.db)
    else:
        if not args.token:
            raise SystemExit("需要 CLOUDSEA_ADMIN_TOKEN 或 --db")
        ok, err = push_via_api(args.api_base, args.token, labels)
        print(f"完成: {ok} 成功, {err} 失败")
        return

    for spot, vp in [("wunvshan", "dianjiangtai"), ("donglingshan", "fengding")]:
        c = count_labels(db, spot, vp)
        print(f"{spot}/{vp}: total={c['total']} full={c.get('full',0)} partial={c.get('partial',0)} none={c.get('none',0)}")


if __name__ == "__main__":
    main()
