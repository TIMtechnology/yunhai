#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_ROOT = ROOT / "data" / "wrf-local"
NOMADS_FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"


def _parse_cycle(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def _forecast_hours(max_hour: int, step: int) -> list[int]:
    return list(range(0, max_hour + 1, step))


def _gfs_url(cycle: datetime, forecast_hour: int, bbox: tuple[float, float, float, float]) -> str:
    leftlon, rightlon, bottomlat, toplat = bbox
    filename = f"gfs.t{cycle:%H}z.pgrb2.0p25.f{forecast_hour:03d}"
    params = {
        "dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos",
        "file": filename,
        "all_lev": "on",
        "all_var": "on",
        "subregion": "",
        "leftlon": f"{leftlon:.2f}",
        "rightlon": f"{rightlon:.2f}",
        "bottomlat": f"{bottomlat:.2f}",
        "toplat": f"{toplat:.2f}",
    }
    return f"{NOMADS_FILTER}?{urlencode(params)}"


def _download(url: str, output: Path, dry_run: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.stat().st_size > 1024:
        print(f"reuse {output}")
        return
    print(f"download {output.name}")
    print(url)
    if dry_run:
        return
    tmp = output.with_suffix(output.suffix + ".tmp")
    cmd = ["curl", "-L", "--fail", "--retry", "5", "--retry-delay", "8", "-o", str(tmp), url]
    subprocess.run(cmd, check=True)
    if tmp.stat().st_size < 1024:
        raise SystemExit(f"downloaded file too small: {tmp}")
    tmp.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download regional GFS GRIB2 files for WRF/WPS.")
    parser.add_argument("--cycle", required=True, help="UTC cycle in YYYYMMDDHH, e.g. 2026060300")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--max-hour", type=int, default=48)
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--lat", type=float, default=41.31976)
    parser.add_argument("--lon", type=float, default=125.40773)
    parser.add_argument("--span-deg", type=float, default=7.0, help="BBox half span in degrees.")
    parser.add_argument("--execute", action="store_true", help="Actually download. Default is dry-run.")
    args = parser.parse_args()

    cycle = _parse_cycle(args.cycle)
    bbox = (
        max(0.0, args.lon - args.span_deg),
        min(360.0, args.lon + args.span_deg),
        max(-90.0, args.lat - args.span_deg),
        min(90.0, args.lat + args.span_deg),
    )
    out_dir = args.work_root / "cache" / "gfs" / cycle.strftime("%Y%m%d%H")
    print(f"GFS cycle: {cycle:%Y%m%d%H}")
    print(f"Output: {out_dir}")
    print(f"BBox: left={bbox[0]:.2f}, right={bbox[1]:.2f}, bottom={bbox[2]:.2f}, top={bbox[3]:.2f}")
    print("Mode:", "execute" if args.execute else "dry-run")

    for hour in _forecast_hours(args.max_hour, args.step):
        filename = f"gfs.t{cycle:%H}z.pgrb2.0p25.f{hour:03d}"
        _download(_gfs_url(cycle, hour, bbox), out_dir / filename, dry_run=not args.execute)


if __name__ == "__main__":
    main()
