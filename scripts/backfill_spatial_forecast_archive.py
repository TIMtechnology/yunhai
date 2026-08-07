#!/usr/bin/env python3
"""Backfill source-aligned valley weather cells for reviewed label dates."""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.sector_meteo import (  # noqa: E402
    build_sector_meteo_index,
    fetch_sector_operational_multi,
    pick_valley_sample_points,
)
from app.engine.coord_transform import weather_coords  # noqa: E402
from app.services.cloudsea_store import (  # noqa: E402
    default_forecast_issue_time,
    init_store,
    load_spatial_forecast_archive,
    save_spatial_forecast_archive,
)
from app.services.meteo_backfill import precursor_hour_keys  # noqa: E402
from app.services.spot_loader import get_spot, get_viewpoint  # noqa: E402
from app.services.terrain_store import resolve_baked_terrain  # noqa: E402
from app.adapters.open_meteo import operational_single_run_source  # noqa: E402
from scripts.train_operational_model import _exact_archive_dates  # noqa: E402


def _dawn_keys(target_date: str) -> set[str]:
    return {
        key
        for key in precursor_hour_keys(target_date)
        if key.startswith(target_date) and 3 <= int(key[11:13]) < 7
    }


def spatial_archive_complete(
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    *,
    db_path: Path,
) -> bool:
    source = operational_single_run_source(date.fromisoformat(target_date))
    index = load_spatial_forecast_archive(
        spot_id,
        viewpoint_id,
        target_date,
        expected_source=source,
        db_path=db_path,
    )
    return _dawn_keys(target_date).issubset(index) and all(index[key] for key in _dawn_keys(target_date))


async def backfill_one(
    *,
    spot_id: str,
    viewpoint_id: str,
    target_date: str,
    db_path: Path,
    force: bool,
) -> dict:
    if not force and spatial_archive_complete(
        spot_id,
        viewpoint_id,
        target_date,
        db_path=db_path,
    ):
        return {"status": "skipped", "date": target_date}

    spot = get_spot(spot_id)
    viewpoint = get_viewpoint(spot_id, viewpoint_id)
    if not spot or not viewpoint:
        return {"status": "failed", "date": target_date, "error": "curated point missing"}
    weather_lat, weather_lng = weather_coords(
        viewpoint.lat,
        viewpoint.lng,
        spot.coord_sys,
    )
    base, profile = resolve_baked_terrain(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        lat=weather_lat,
        lng=weather_lng,
        profile_date=date.fromisoformat(target_date),
    )
    if not base or not profile:
        return {"status": "failed", "date": target_date, "error": "terrain snapshot missing"}
    points = pick_valley_sample_points(
        profile,
        viewer_elev_m=float(viewpoint.elevation),
    )
    if not points:
        return {"status": "failed", "date": target_date, "error": "no valley samples"}

    last_error = ""
    for attempt in range(4):
        try:
            forecasts = await fetch_sector_operational_multi(
                points,
                target_date=date.fromisoformat(target_date),
            )
            index = build_sector_meteo_index(forecasts, points)
            dawn = {key: index[key] for key in _dawn_keys(target_date) if index.get(key)}
            if set(dawn) != _dawn_keys(target_date):
                raise ValueError(f"dawn spatial hours incomplete: {len(dawn)}/4")
            source = operational_single_run_source(date.fromisoformat(target_date))
            saved = save_spatial_forecast_archive(
                spot_id=spot_id,
                viewpoint_id=viewpoint_id,
                target_date=target_date,
                issue_time=default_forecast_issue_time(target_date),
                source=source,
                index=dawn,
                db_path=db_path,
            )
            return {"status": "ok", "date": target_date, "rows_saved": saved}
        except Exception as exc:  # network/API errors are reported per date
            last_error = str(exc)
            if attempt < 3:
                await asyncio.sleep(3.0 * (attempt + 1))
    return {"status": "failed", "date": target_date, "error": last_error}


async def _run(args: argparse.Namespace, db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT spot_id, viewpoint_id, date
            FROM cloudsea_labels
            WHERE (review_status='approved' OR review_status IS NULL)
              AND spot_id=? AND viewpoint_id=?
            ORDER BY date
            """,
            (args.spot_id, args.viewpoint_id),
        ).fetchall()
    exact_dates = _exact_archive_dates(db_path, args.spot_id, args.viewpoint_id)
    rows = [row for row in rows if str(row[2]) in exact_dates]

    missing = [
        str(row[2])
        for row in rows
        if not spatial_archive_complete(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            db_path=db_path,
        )
    ]
    if args.check:
        print(f"标注日 {len(rows)}，缺失 exact spatial archive {len(missing)}")
        for target in missing:
            print(f"  - {target}")
        return

    ok = skipped = failed = 0
    for spot_id, viewpoint_id, target_date in rows:
        result = await backfill_one(
            spot_id=str(spot_id),
            viewpoint_id=str(viewpoint_id),
            target_date=str(target_date),
            db_path=db_path,
            force=args.force,
        )
        status = result["status"]
        ok += status == "ok"
        skipped += status == "skipped"
        failed += status == "failed"
        print(result)
        if args.sleep > 0:
            await asyncio.sleep(args.sleep)
    print(f"完成: ok={ok} skipped={skipped} failed={failed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill exact spatial forecast archive")
    parser.add_argument("--db", default=str(ROOT / "data/cloudsea/cloudsea.prod.db"))
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument("--sleep", type=float, default=1.5)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    db_path = Path(args.db).resolve()
    os.environ["CLOUDSEA_DB_PATH"] = str(db_path)
    from app.config import settings

    settings.cloudsea_db_path = str(db_path)
    init_store()
    asyncio.run(_run(args, db_path))


if __name__ == "__main__":
    main()
