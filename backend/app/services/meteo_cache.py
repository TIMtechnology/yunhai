"""标注库 meteo 复用：从 DB 重建 Open-Meteo hourly 结构，跳过历史 API。"""

from __future__ import annotations

from typing import Any

from app.engine.utils import parse_shanghai_time

# raw_json 字段 → Open-Meteo hourly 键
_ROW_TO_HOURLY = {
    "temp": "temperature_2m",
    "dewpoint": "dew_point_2m",
    "rh": "relative_humidity_2m",
    "cloud_low": "cloud_cover_low",
    "cloud_mid": "cloud_cover_mid",
    "cloud_high": "cloud_cover_high",
    "wind": "wind_speed_10m",
    "visibility": "visibility",
    "precipitation": "precipitation",
    "rh_850": "relative_humidity_850hPa",
    "rh_700": "relative_humidity_700hPa",
    "t_850": "temperature_850hPa",
    "t_925": "temperature_925hPa",
}


def rows_to_hourly(rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """将 meteo_hourly / meteo_day 行转为 build_predictions_from_hourly 可用的 hourly dict。"""
    ordered = sorted(rows, key=lambda r: str(r.get("time") or ""))
    hourly: dict[str, list[Any]] = {"time": []}
    for row in ordered:
        t_str = str(row.get("time") or "")
        if not t_str:
            continue
        hourly["time"].append(t_str)
        for src, dest in _ROW_TO_HOURLY.items():
            hourly.setdefault(dest, []).append(row.get(src))
        low = row.get("cloud_low")
        mid = row.get("cloud_mid")
        high = row.get("cloud_high")
        total = None
        if low is not None and mid is not None:
            total = (float(low) + float(mid) + float(high or 0)) / (3.0 if high is not None else 2.0)
        hourly.setdefault("cloud_cover", []).append(total)
    return hourly


def is_day_meteo_complete(
    rows: list[dict[str, Any]],
    *,
    min_hours: int = 20,
    require_pressure: bool = True,
) -> bool:
    if len(rows) < min_hours:
        return False
    if not require_pressure:
        return True
    with_rh850 = sum(1 for r in rows if r.get("rh_850") is not None)
    return with_rh850 >= min(min_hours, len(rows))


def astronomy_from_bundle(bundle: dict[str, Any] | None, date_key: str) -> dict[str, dict]:
    if not bundle:
        return {}
    entry = bundle.get(date_key) or bundle.get("daily")
    if not entry:
        return {}
    out: dict[str, Any] = {}
    sunrise = entry.get("sunrise")
    sunset = entry.get("sunset")
    if sunrise:
        out["sunrise"] = parse_shanghai_time(str(sunrise))
    if sunset:
        out["sunset"] = parse_shanghai_time(str(sunset))
    return {date_key: out} if out else {}


def serialize_astronomy_for_store(astronomy: dict[str, dict]) -> dict[str, dict[str, str]]:
    stored: dict[str, dict[str, str]] = {}
    for dk, entry in astronomy.items():
        stored[dk] = {
            k: v.isoformat() if hasattr(v, "isoformat") else str(v)
            for k, v in entry.items()
        }
    return stored


def hour_rows_from_hourly(hourly: dict[str, Any], date_key: str) -> list[dict[str, Any]]:
    """从 Open-Meteo hourly 切片生成可入库的逐时 raw 行（全日）。"""
    from app.engine.cloudsea_features import hour_raw_from_forecast

    times = hourly.get("time") or []
    rows: list[dict[str, Any]] = []
    for idx, t_str in enumerate(times):
        if not str(t_str).startswith(date_key):
            continue
        rows.append(
            hour_raw_from_forecast(
                t_str=t_str,
                idx=idx,
                cloud_low=hourly.get("cloud_cover_low", []),
                cloud_mid=hourly.get("cloud_cover_mid", []),
                cloud_high=hourly.get("cloud_cover_high", []),
                visibilities=hourly.get("visibility", []),
                rhs=hourly.get("relative_humidity_2m", []),
                rh_850_series=hourly.get("relative_humidity_850hPa", []),
                rh_700_series=hourly.get("relative_humidity_700hPa", []),
                t_850_series=hourly.get("temperature_850hPa", []),
                t_925_series=hourly.get("temperature_925hPa", []),
                winds=hourly.get("wind_speed_10m", []),
                precips=hourly.get("precipitation", []),
                temps=hourly.get("temperature_2m", []),
                dews=hourly.get("dew_point_2m", []),
            )
        )
    return rows


def precursor_rows_from_hourly(
    hourly: dict[str, Any],
    target_date: str,
) -> list[dict[str, Any]]:
    """Build the fixed evening/night/dawn rows used by operational ML."""
    from app.engine.cloudsea_features import build_meteo_hour_row
    from app.services.meteo_backfill import precursor_hour_keys

    expected = set(precursor_hour_keys(target_date))
    rows: list[dict[str, Any]] = []
    for idx, t_str in enumerate(hourly.get("time") or []):
        if str(t_str) not in expected:
            continue
        rows.append(build_meteo_hour_row(hourly, idx))
    return sorted(rows, key=lambda row: str(row.get("time") or ""))


def operational_precursor_rows_complete(
    rows: list[dict[str, Any]],
    target_date: str,
) -> bool:
    """Require every expected hour and every field used by the operational model."""
    from app.engine.cloudsea_features import operational_meteo_row_complete
    from app.services.meteo_backfill import precursor_hour_keys

    complete_times = {
        str(row.get("time") or "")
        for row in rows
        if operational_meteo_row_complete(row)
    }
    return set(precursor_hour_keys(target_date)).issubset(complete_times)


def overlay_hourly_with_operational_rows(
    hourly: dict[str, Any],
    operational_rows_by_date: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Overlay exact single-run surface weather onto the hours used for scoring.

    Pressure-level values are deliberately cleared for overlaid hours because the
    reviewed ECMWF IFS HRES archive contains surface fields only. Keeping values
    from the generic best-match forecast would silently mix two model products.
    """
    scoring: dict[str, Any] = {
        key: list(value) if isinstance(value, list) else value
        for key, value in hourly.items()
    }
    times = list(scoring.get("time") or [])
    if not times:
        return scoring
    index_by_time = {str(value): idx for idx, value in enumerate(times)}

    for rows in operational_rows_by_date.values():
        for row in rows:
            idx = index_by_time.get(str(row.get("time") or ""))
            if idx is None:
                continue
            for source, destination in _ROW_TO_HOURLY.items():
                series = scoring.get(destination)
                if not isinstance(series, list) or len(series) != len(times):
                    series = [None] * len(times)
                    scoring[destination] = series
                series[idx] = row.get(source)

            cloud_parts = [
                row.get("cloud_low"),
                row.get("cloud_mid"),
                row.get("cloud_high"),
            ]
            cloud_total = scoring.get("cloud_cover")
            if not isinstance(cloud_total, list) or len(cloud_total) != len(times):
                cloud_total = [None] * len(times)
                scoring["cloud_cover"] = cloud_total
            cloud_total[idx] = (
                sum(float(value) for value in cloud_parts) / len(cloud_parts)
                if all(value is not None for value in cloud_parts)
                else None
            )
    return scoring
