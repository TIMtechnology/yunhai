#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_ROOT = ROOT / "data" / "wrf-local"


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result):
        return None
    return result


def _decode_times(times: xr.DataArray) -> list[str]:
    values = times.values
    decoded: list[str] = []
    for item in values:
        if isinstance(item, bytes):
            text = item.decode("utf-8")
        elif getattr(item, "dtype", None) is not None and item.dtype.kind in {"S", "U"}:
            text = b"".join(item).decode("utf-8") if item.dtype.kind == "S" else "".join(item.tolist())
        else:
            text = str(item)
        decoded.append(text.strip())
    return decoded


def _nearest_grid(ds: xr.Dataset, lat: float, lon: float) -> tuple[int, int, float]:
    lats = ds["XLAT"].isel(Time=0).values if "Time" in ds["XLAT"].dims else ds["XLAT"].values
    lons = ds["XLONG"].isel(Time=0).values if "Time" in ds["XLONG"].dims else ds["XLONG"].values
    distance = (lats - lat) ** 2 + (lons - lon) ** 2
    y, x = np.unravel_index(np.nanargmin(distance), distance.shape)
    return int(y), int(x), float(np.sqrt(distance[y, x]))


def _relative_humidity(t2_k: float | None, q2: float | None, psfc_pa: float | None) -> float | None:
    if t2_k is None or q2 is None or psfc_pa is None:
        return None
    temp_c = t2_k - 273.15
    vapor_pressure = (q2 * psfc_pa) / (0.622 + q2) / 100.0
    saturation = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    return max(0.0, min(100.0, 100.0 * vapor_pressure / saturation))


def _wind_speed(u: float | None, v: float | None) -> float | None:
    if u is None or v is None:
        return None
    return math.sqrt(u * u + v * v)


def _wind_direction(u: float | None, v: float | None) -> float | None:
    if u is None or v is None:
        return None
    # Meteorological direction: where the wind comes from.
    return (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0


def _profile_heights(ds: xr.Dataset, time_idx: int, y: int, x: int) -> np.ndarray | None:
    if "PH" not in ds or "PHB" not in ds:
        return None
    ph = ds["PH"].isel(Time=time_idx, south_north=y, west_east=x).values
    phb = ds["PHB"].isel(Time=time_idx, south_north=y, west_east=x).values
    staggered = (ph + phb) / 9.80665
    mass_levels = 0.5 * (staggered[:-1] + staggered[1:])
    hgt = float(ds["HGT"].isel(Time=time_idx, south_north=y, west_east=x).values) if "HGT" in ds else 0.0
    return mass_levels - hgt


def _profile_temperature_c(ds: xr.Dataset, time_idx: int, y: int, x: int) -> np.ndarray | None:
    if "T" not in ds or "P" not in ds or "PB" not in ds:
        return None
    theta = ds["T"].isel(Time=time_idx, south_north=y, west_east=x).values + 300.0
    pressure = ds["P"].isel(Time=time_idx, south_north=y, west_east=x).values + ds["PB"].isel(
        Time=time_idx, south_north=y, west_east=x
    ).values
    temp_k = theta * (pressure / 100000.0) ** 0.2854
    return temp_k - 273.15


def _cloud_layers(ds: xr.Dataset, time_idx: int, y: int, x: int, heights_agl: np.ndarray | None) -> dict[str, float | None]:
    if "CLDFRA" not in ds:
        return {"low": None, "mid": None, "high": None}
    cloud = ds["CLDFRA"].isel(Time=time_idx, south_north=y, west_east=x).values
    if heights_agl is None or len(heights_agl) != len(cloud):
        thirds = np.array_split(cloud, 3)
        return {
            "low": _as_float(np.nanmax(thirds[0]) * 100.0),
            "mid": _as_float(np.nanmax(thirds[1]) * 100.0),
            "high": _as_float(np.nanmax(thirds[2]) * 100.0),
        }
    return {
        "low": _as_float(np.nanmax(cloud[heights_agl <= 2000.0]) * 100.0) if np.any(heights_agl <= 2000.0) else None,
        "mid": _as_float(np.nanmax(cloud[(heights_agl > 2000.0) & (heights_agl <= 6000.0)]) * 100.0)
        if np.any((heights_agl > 2000.0) & (heights_agl <= 6000.0))
        else None,
        "high": _as_float(np.nanmax(cloud[heights_agl > 6000.0]) * 100.0) if np.any(heights_agl > 6000.0) else None,
    }


def _cloud_base_top(ds: xr.Dataset, time_idx: int, y: int, x: int, heights_agl: np.ndarray | None) -> dict[str, float | None]:
    if "CLDFRA" not in ds or heights_agl is None:
        return {"base_m_agl": None, "top_m_agl": None}
    cloud = ds["CLDFRA"].isel(Time=time_idx, south_north=y, west_east=x).values
    mask = cloud >= 0.20
    if not np.any(mask):
        return {"base_m_agl": None, "top_m_agl": None}
    levels = heights_agl[mask]
    return {"base_m_agl": _as_float(np.nanmin(levels)), "top_m_agl": _as_float(np.nanmax(levels))}


def _inversion_strength(ds: xr.Dataset, time_idx: int, y: int, x: int, heights_agl: np.ndarray | None) -> float | None:
    temp_c = _profile_temperature_c(ds, time_idx, y, x)
    if temp_c is None or heights_agl is None:
        return None
    mask = (heights_agl >= 100.0) & (heights_agl <= 1800.0)
    if np.count_nonzero(mask) < 2:
        return None
    temps = temp_c[mask]
    return _as_float(np.nanmax(temps[1:] - temps[:-1]))


def _time_record(ds: xr.Dataset, time_idx: int, y: int, x: int, timestamp: str) -> dict[str, Any]:
    t2 = _as_float(ds["T2"].isel(Time=time_idx, south_north=y, west_east=x).values) if "T2" in ds else None
    q2 = _as_float(ds["Q2"].isel(Time=time_idx, south_north=y, west_east=x).values) if "Q2" in ds else None
    psfc = _as_float(ds["PSFC"].isel(Time=time_idx, south_north=y, west_east=x).values) if "PSFC" in ds else None
    u10 = _as_float(ds["U10"].isel(Time=time_idx, south_north=y, west_east=x).values) if "U10" in ds else None
    v10 = _as_float(ds["V10"].isel(Time=time_idx, south_north=y, west_east=x).values) if "V10" in ds else None
    rain = None
    if "RAINC" in ds and "RAINNC" in ds:
        rain = _as_float(
            ds["RAINC"].isel(Time=time_idx, south_north=y, west_east=x).values
            + ds["RAINNC"].isel(Time=time_idx, south_north=y, west_east=x).values
        )
    heights = _profile_heights(ds, time_idx, y, x)
    return {
        "time": timestamp,
        "temperature_2m_c": _as_float(t2 - 273.15) if t2 is not None else None,
        "relative_humidity_2m": _relative_humidity(t2, q2, psfc),
        "surface_pressure_hpa": _as_float(psfc / 100.0) if psfc is not None else None,
        "wind_speed_10m": _wind_speed(u10, v10),
        "wind_direction_10m": _wind_direction(u10, v10),
        "accumulated_rain_mm": rain,
        "cloud_layers": _cloud_layers(ds, time_idx, y, x, heights),
        "cloud_boundary": _cloud_base_top(ds, time_idx, y, x, heights),
        "low_level_inversion_c": _inversion_strength(ds, time_idx, y, x, heights),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract cloudsea evidence from WRF wrfout files.")
    parser.add_argument("wrfout", type=Path, nargs="+")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--case", default="wunvshan")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    hourly: list[dict[str, Any]] = []
    nearest: dict[str, Any] | None = None
    for wrfout in sorted(args.wrfout):
        with xr.open_dataset(wrfout) as ds:
            y, x, distance = _nearest_grid(ds, args.lat, args.lon)
            if nearest is None:
                nearest = {"south_north": y, "west_east": x, "degree_distance": distance}
            times = _decode_times(ds["Times"]) if "Times" in ds else [str(item) for item in ds["Time"].values]
            hourly.extend(_time_record(ds, idx, y, x, timestamp) for idx, timestamp in enumerate(times))

    if nearest is None:
        raise SystemExit("no wrfout files provided")

    hourly.sort(key=lambda item: item.get("time") or "")

    output = {
        "case": args.case,
        "source": "wrf",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "wrfout": [str(path) for path in sorted(args.wrfout)],
        "target": {"lat": args.lat, "lon": args.lon},
        "nearest_grid": nearest,
        "hourly": hourly,
    }
    out_path = args.output or args.wrfout[0].with_suffix(".cloudsea-evidence.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
