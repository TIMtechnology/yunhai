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
