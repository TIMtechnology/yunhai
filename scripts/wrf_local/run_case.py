#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CASES_FILE = Path(__file__).with_name("cases.json")
DEFAULT_WORK_ROOT = ROOT / "data" / "wrf-local"
SCENIC_ROOT = ROOT / "data" / "scenic-spots"
DOWNLOAD_GFS = Path(__file__).with_name("download_gfs.py")


@dataclass(frozen=True)
class Viewpoint:
    spot_id: str
    viewpoint_id: str
    name: str
    lat: float
    lng: float
    elevation: float


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_cycle(value: str | None) -> datetime:
    if not value:
        now = datetime.now(timezone.utc)
        cycle_hour = (now.hour // 6) * 6
        return now.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def _load_viewpoint(spot_id: str, viewpoint_id: str) -> Viewpoint:
    spot = _load_json(SCENIC_ROOT / f"{spot_id}.json")
    vp = next((item for item in spot.get("viewpoints", []) if item.get("id") == viewpoint_id), None)
    if not vp:
        raise SystemExit(f"viewpoint not found: {spot_id}/{viewpoint_id}")
    return Viewpoint(
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        name=f"{spot.get('name', spot_id)}·{vp.get('name', viewpoint_id)}",
        lat=float(vp["lat"]),
        lng=float(vp["lng"]),
        elevation=float(vp.get("elevation") or 0),
    )


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d_%H:%M:%S")


def _namelist_wps(case: dict[str, Any], vp: Viewpoint, start: datetime, end: datetime) -> str:
    domain = case["domain"]
    return f"""&share
 wrf_core = 'ARW',
 max_dom = 1,
 start_date = '{_fmt_dt(start)}',
 end_date   = '{_fmt_dt(end)}',
 interval_seconds = 10800,
 io_form_geogrid = 2,
/

&geogrid
 parent_id         = 1,
 parent_grid_ratio = 1,
 i_parent_start    = 1,
 j_parent_start    = 1,
 e_we              = {domain["e_we"]},
 e_sn              = {domain["e_sn"]},
 geog_data_res     = 'default',
 dx = {domain["dx"]},
 dy = {domain["dy"]},
 map_proj = '{domain["map_proj"]}',
 ref_lat   = {vp.lat:.5f},
 ref_lon   = {vp.lng:.5f},
 truelat1  = {domain["truelat1"]},
 truelat2  = {domain["truelat2"]},
 stand_lon = {domain["stand_lon"]},
 geog_data_path = '/work/fixed/geog',
/

&ungrib
 out_format = 'WPS',
 prefix = 'FILE',
/

&metgrid
 fg_name = 'FILE',
 io_form_metgrid = 2,
/
"""


def _namelist_input(case: dict[str, Any], start: datetime, end: datetime) -> str:
    domain = case["domain"]
    run_hours = int((end - start).total_seconds() // 3600)
    return f"""&time_control
 run_days                            = 0,
 run_hours                           = {run_hours},
 run_minutes                         = 0,
 run_seconds                         = 0,
 start_year                          = {start.year},
 start_month                         = {start.month},
 start_day                           = {start.day},
 start_hour                          = {start.hour},
 end_year                            = {end.year},
 end_month                           = {end.month},
 end_day                             = {end.day},
 end_hour                            = {end.hour},
 interval_seconds                    = 10800,
 input_from_file                     = .true.,
 history_interval                    = {case["output_interval_minutes"]},
 frames_per_outfile                  = 1,
 restart                             = .false.,
 io_form_history                     = 2,
 io_form_restart                     = 2,
 io_form_input                       = 2,
 io_form_boundary                    = 2,
/

&domains
 time_step                           = 54,
 max_dom                             = 1,
 e_we                                = {domain["e_we"]},
 e_sn                                = {domain["e_sn"]},
 e_vert                              = 35,
 num_metgrid_levels                  = 34,
 num_metgrid_soil_levels             = 4,
 p_top_requested                     = 5000,
 dx                                  = {domain["dx"]},
 dy                                  = {domain["dy"]},
 grid_id                             = 1,
 parent_id                           = 0,
 i_parent_start                      = 1,
 j_parent_start                      = 1,
 parent_grid_ratio                   = 1,
 parent_time_step_ratio              = 1,
 feedback                            = 0,
 smooth_option                       = 0,
/

&physics
 physics_suite                       = 'CONUS',
 mp_physics                          = 8,
 ra_lw_physics                       = 4,
 ra_sw_physics                       = 4,
 radt                                = 9,
 sf_sfclay_physics                   = 1,
 sf_surface_physics                  = 2,
 bl_pbl_physics                      = 1,
 bldt                                = 0,
 cu_physics                          = 1,
 cudt                                = 5,
 num_soil_layers                     = 4,
/

&dynamics
 hybrid_opt                          = 2,
 w_damping                           = 0,
 diff_opt                            = 1,
 km_opt                              = 4,
 diff_6th_opt                        = 0,
 base_temp                           = 290.,
 damp_opt                            = 3,
 zdamp                               = 5000.,
 dampcoef                            = 0.2,
 khdif                               = 0,
 kvdif                               = 0,
 non_hydrostatic                     = .true.,
 moist_adv_opt                       = 1,
 scalar_adv_opt                      = 1,
/

&bdy_control
 spec_bdy_width                      = 5,
 specified                          = .true.,
/

&namelist_quilt
 nio_tasks_per_group = 0,
 nio_groups = 1,
/
"""


def _write_manifest(run_dir: Path, case_name: str, case: dict[str, Any], vp: Viewpoint, cycle: datetime, end: datetime) -> None:
    manifest = {
        "case": case_name,
        "spot_id": vp.spot_id,
        "viewpoint_id": vp.viewpoint_id,
        "name": vp.name,
        "lat": vp.lat,
        "lng": vp.lng,
        "elevation": vp.elevation,
        "cycle_utc": cycle.strftime("%Y%m%d%H"),
        "end_utc": end.strftime("%Y%m%d%H"),
        "forecast_hours": case["forecast_hours"],
        "domain": case["domain"],
        "status": "prepared",
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_command(cmd: list[str], cwd: Path, dry_run: bool) -> None:
    print("$", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, cwd=cwd, check=True)


def _link_force(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        return
    dst.symlink_to(src, target_is_directory=src.is_dir())


def _prepare_runtime_links(run_dir: Path, work_root: Path) -> None:
    fixed = work_root / "fixed"
    bin_dir = fixed / "bin"
    wps_dir = fixed / "WPS"
    wrf_dir = fixed / "WRF"
    required = [
        bin_dir / "geogrid.exe",
        bin_dir / "ungrib.exe",
        bin_dir / "metgrid.exe",
        bin_dir / "link_grib.csh",
        bin_dir / "real.exe",
        bin_dir / "wrf.exe",
        wps_dir / "geogrid",
        wps_dir / "ungrib",
        wps_dir / "metgrid",
        wrf_dir / "run",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(
            "WRF/WPS runtime is not ready. Run scripts/wrf_local/build_wrf_stack.sh inside the container first.\n"
            + "\n".join(missing)
        )
    for name in ("geogrid.exe", "ungrib.exe", "metgrid.exe", "link_grib.csh", "real.exe", "wrf.exe"):
        _link_force(bin_dir / name, run_dir / name)
    for name in ("geogrid", "ungrib", "metgrid"):
        _link_force(wps_dir / name, run_dir / name)
    vtable = wps_dir / "ungrib" / "Variable_Tables" / "Vtable.GFS"
    _link_force(vtable, run_dir / "Vtable")
    for src in (wrf_dir / "run").iterdir():
        if src.name in {"README.namelist", "README.physics_files"}:
            continue
        _link_force(src, run_dir / src.name)


def _download_gfs(cycle: datetime, case: dict[str, Any], vp: Viewpoint, work_root: Path, dry_run: bool) -> None:
    span_lng = float(case.get("cloud_region", {}).get("span_lng") or 2.0)
    span_lat = float(case.get("cloud_region", {}).get("span_lat") or 1.4)
    span = max(5.0, span_lng * 2.5, span_lat * 3.0)
    cmd = [
        "python3",
        str(DOWNLOAD_GFS),
        "--cycle",
        cycle.strftime("%Y%m%d%H"),
        "--work-root",
        str(work_root),
        "--max-hour",
        str(int(case["forecast_hours"])),
        "--step",
        "3",
        "--lat",
        str(vp.lat),
        "--lon",
        str(vp.lng),
        "--span-deg",
        f"{span:.1f}",
    ]
    if not dry_run:
        cmd.append("--execute")
    _run_command(cmd, ROOT, dry_run=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare or run a local WRF case for Yunhai regional evidence.")
    parser.add_argument("--case", choices=sorted(_load_json(CASES_FILE).keys()), default="wunvshan")
    parser.add_argument("--cycle", help="UTC cycle in YYYYMMDDHH. Defaults to latest 6-hour cycle.")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--execute", action="store_true", help="Actually execute WPS/WRF commands. Default is dry-run.")
    parser.add_argument("--download-gfs", action="store_true", help="Download regional GFS files for this WRF case.")
    parser.add_argument(
        "--wrf-processes",
        type=int,
        default=int(os.environ.get("WRF_PROCESSES", "1")),
        help="Number of WRF MPI processes. Use 1 for serial builds; use 3 for dmpar on the target server.",
    )
    args = parser.parse_args()

    cases = _load_json(CASES_FILE)
    case = cases[args.case]
    vp = _load_viewpoint(case["spot_id"], case["viewpoint_id"])
    cycle = _parse_cycle(args.cycle)
    end = cycle + timedelta(hours=int(case["forecast_hours"]))

    work_root = args.work_root.resolve()
    run_id = cycle.strftime("%Y%m%d%H")
    run_dir = work_root / "runs" / args.case / run_id
    fixed_dir = work_root / "fixed"
    gfs_dir = work_root / "cache" / "gfs" / run_id
    products_dir = work_root / "products" / args.case
    for path in (run_dir, fixed_dir, gfs_dir, products_dir):
        path.mkdir(parents=True, exist_ok=True)

    (run_dir / "namelist.wps").write_text(_namelist_wps(case, vp, cycle, end), encoding="utf-8")
    (run_dir / "namelist.input").write_text(_namelist_input(case, cycle, end), encoding="utf-8")
    _write_manifest(run_dir, args.case, case, vp, cycle, end)

    print(f"Prepared WRF case: {args.case} ({vp.name})")
    print(f"Run directory: {run_dir}")
    print(f"GFS cache: {gfs_dir}")
    print(f"Fixed cache: {fixed_dir}")
    print("Mode:", "execute" if args.execute else "dry-run")

    if args.download_gfs:
        _download_gfs(cycle, case, vp, work_root, dry_run=not args.execute)

    if args.execute:
        _prepare_runtime_links(run_dir, work_root)
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        jasper_lib = str(work_root / "fixed" / "lib" / "lib")
        current_ld_path = os.environ.get("LD_LIBRARY_PATH")
        os.environ["LD_LIBRARY_PATH"] = f"{jasper_lib}:{current_ld_path}" if current_ld_path else jasper_lib

    wrf_command = ["./wrf.exe"] if args.wrf_processes <= 1 else ["mpirun", "-np", str(args.wrf_processes), "./wrf.exe"]
    commands = [
        ["./geogrid.exe"],
        ["./link_grib.csh", str(gfs_dir / "gfs.t*.pgrb2.0p25.f*")],
        ["./ungrib.exe"],
        ["./metgrid.exe"],
        ["./real.exe"],
        wrf_command,
    ]
    for cmd in commands:
        _run_command(cmd, run_dir, dry_run=not args.execute)


if __name__ == "__main__":
    main()
