#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_ROOT = ROOT / "data" / "wrf-local"
DEFAULT_KEEP_GFS_DAYS = 3
DEFAULT_KEEP_RUN_DAYS = 7
DEFAULT_KEEP_PRODUCTS_DAYS = 30


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _iter_children(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(path.iterdir(), key=lambda item: item.stat().st_mtime)


def _remove_old_children(path: Path, keep_days: int, dry_run: bool) -> tuple[int, int]:
    cutoff = _cutoff(keep_days)
    removed = 0
    freed = 0
    for child in _iter_children(path):
        if _modified_at(child) >= cutoff:
            continue
        size = _size_bytes(child)
        print(f"remove {child} ({size / 1024 / 1024:.1f} MiB)")
        if not dry_run:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        removed += 1
        freed += size
    return removed, freed


def _size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _ensure_free_space(path: Path, min_free_gb: float) -> None:
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    free_gb = usage.free / 1024 / 1024 / 1024
    print(f"free space at {path}: {free_gb:.1f} GiB")
    if free_gb < min_free_gb:
        raise SystemExit(f"free space below threshold: {free_gb:.1f} GiB < {min_free_gb:.1f} GiB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean local WRF cache and run artifacts.")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--keep-gfs-days", type=int, default=DEFAULT_KEEP_GFS_DAYS)
    parser.add_argument("--keep-run-days", type=int, default=DEFAULT_KEEP_RUN_DAYS)
    parser.add_argument("--keep-products-days", type=int, default=DEFAULT_KEEP_PRODUCTS_DAYS)
    parser.add_argument("--min-free-gb", type=float, default=12.0)
    parser.add_argument("--execute", action="store_true", help="Delete files. Default only prints planned removals.")
    args = parser.parse_args()

    dry_run = not args.execute
    _ensure_free_space(args.work_root, args.min_free_gb)

    targets = [
        (args.work_root / "cache" / "gfs", args.keep_gfs_days),
        (args.work_root / "runs", args.keep_run_days),
        (args.work_root / "products", args.keep_products_days),
        (args.work_root / "tmp", args.keep_run_days),
    ]
    total_removed = 0
    total_freed = 0
    for path, keep_days in targets:
        removed, freed = _remove_old_children(path, keep_days, dry_run=dry_run)
        total_removed += removed
        total_freed += freed

    mode = "dry-run" if dry_run else "execute"
    print(f"{mode}: removed={total_removed}, reclaimable={total_freed / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
