from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.engine.cloudsea_features import meteo_row_complete

SUNRISE_WINDOW_START = 3
SUNRISE_WINDOW_END = 7
RAIN_PRECIP_MM = 0.1


def min_labels_for_ml() -> int:
    return settings.cloudsea_ml_min_labels_per_spot


def spot_model_filename(spot_id: str, viewpoint_id: str) -> str:
    safe = f"{spot_id}_{viewpoint_id}".replace("/", "_")
    return f"spot_{safe}.pkl"


def spot_model_path(spot_id: str, viewpoint_id: str, *, models_dir: Path | None = None) -> Path:
    base = models_dir or Path(settings.cloudsea_model_path).resolve().parent
    return base / spot_model_filename(spot_id, viewpoint_id)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.cloudsea_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_meteo_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows:
        raw = json.loads(row["raw_json"])
        if isinstance(raw, dict):
            parsed.append(raw)
    return parsed


def load_sunrise_window_meteo(
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    window_start: int = SUNRISE_WINDOW_START,
    window_end: int = SUNRISE_WINDOW_END,
) -> list[dict[str, Any]]:
    """从 meteo_hourly 读取某日日出窗口逐时数据（含 precipitation 若已存）。"""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT raw_json FROM meteo_hourly
            WHERE spot_id=? AND viewpoint_id=? AND ts LIKE ?
            ORDER BY ts
            """,
            (spot_id, viewpoint_id, f"{date_key}T%"),
        ).fetchall()
    hour_rows = _parse_meteo_rows(rows)
    filtered: list[dict[str, Any]] = []
    for row in hour_rows:
        time_str = str(row.get("time") or "")
        if "T" not in time_str:
            continue
        hour = int(time_str[11:13])
        if window_start <= hour < window_end:
            filtered.append(row)
    return filtered


def sunrise_window_rain_summary(hour_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rainy_hours: list[str] = []
    max_precip = 0.0
    total_precip = 0.0
    for row in hour_rows:
        precip_raw = row.get("precipitation")
        if precip_raw is None:
            continue
        precip = float(precip_raw)
        if precip >= RAIN_PRECIP_MM:
            time_str = str(row.get("time") or "")
            hour_label = time_str[11:16] if "T" in time_str else time_str
            rainy_hours.append(hour_label)
            max_precip = max(max_precip, precip)
            total_precip += precip
    has_rain = len(rainy_hours) > 0
    return {
        "has_rain": has_rain,
        "rainy_hours": rainy_hours,
        "max_precip_mm": round(max_precip, 2),
        "total_precip_mm": round(total_precip, 2),
        "excluded_from_training": has_rain,
        "hint": (
            "日出窗口（03:00–07:00）内有降水，建议直接标注「无云海」；"
            "该日不计入 ML 训练有效样本，也不计入 30 日达标计数"
            if has_rain
            else ""
        ),
    }


def is_label_approved(label: dict[str, Any]) -> bool:
    status = label.get("review_status")
    return status in (None, "approved")


def is_training_eligible_label(
    label: dict[str, Any],
    hour_rows: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    if not is_label_approved(label):
        return False, "未审核通过"
    if hour_rows is None:
        hour_rows = load_sunrise_window_meteo(
            label["spot_id"],
            label["viewpoint_id"],
            label["date"],
            window_start=int(label.get("window_start") or SUNRISE_WINDOW_START),
            window_end=int(label.get("window_end") or SUNRISE_WINDOW_END),
        )
    if not hour_rows:
        return False, "缺少日出窗口气象"
    if not all(meteo_row_complete(r) for r in hour_rows):
        return False, "气象字段不完整"
    rain = sunrise_window_rain_summary(hour_rows)
    if rain["has_rain"]:
        return False, "日出窗口有降水"
    return True, "ok"


def count_training_eligible_labels(
    spot_id: str,
    viewpoint_id: str,
) -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM cloudsea_labels
            WHERE spot_id=? AND viewpoint_id=?
            ORDER BY date
            """,
            (spot_id, viewpoint_id),
        ).fetchall()
    total = len(rows)
    eligible = 0
    rain_excluded = 0
    for raw in rows:
        label = dict(raw)
        if not is_label_approved(label):
            continue
        hour_rows = load_sunrise_window_meteo(spot_id, viewpoint_id, label["date"])
        if hour_rows and sunrise_window_rain_summary(hour_rows)["has_rain"]:
            rain_excluded += 1
            continue
        eligible += 1
    return {
        "total_labels": total,
        "eligible_labels": eligible,
        "rain_excluded_labels": rain_excluded,
        "min_labels": min_labels_for_ml(),
    }


def build_ml_status(
    spot_id: str | None,
    viewpoint_id: str | None,
    *,
    has_spot_model: bool | None = None,
    model_trained_days: int | None = None,
) -> dict[str, Any]:
    min_labels = min_labels_for_ml()
    if not spot_id or not viewpoint_id:
        return {
            "ml_active": False,
            "mode": "rule_only",
            "min_labels": min_labels,
            "eligible_labels": 0,
            "total_labels": 0,
            "rain_excluded_labels": 0,
            "has_spot_model": False,
            "message": "未绑定观景点，03–07 点仅使用规则引擎",
        }

    counts = count_training_eligible_labels(spot_id, viewpoint_id)
    if has_spot_model is None:
        has_spot_model = spot_model_path(spot_id, viewpoint_id).is_file()
        if spot_id == "wunvshan" and not has_spot_model:
            has_spot_model = Path(settings.cloudsea_model_path).is_file()

    eligible = counts["eligible_labels"]
    trained_days = model_trained_days or 0
    model_ready = has_spot_model and max(eligible, trained_days) >= min_labels

    if model_ready:
        if spot_id == "wunvshan":
            mode = "wunvshan_model"
            message = f"本点位有效标注 {eligible} 天，已启用 ML + 规则融合（五女山模型）"
        else:
            mode = "spot_model"
            message = f"本点位有效标注 {eligible} 天，已启用该点位专属 ML + 规则融合"
    else:
        mode = "rule_only"
        if eligible < min_labels:
            rain_note = (
                f"，其中 {counts['rain_excluded_labels']} 天因日出时段有雨已排除"
                if counts["rain_excluded_labels"]
                else ""
            )
            message = (
                f"本点位 ML 有效标注 {eligible}/{min_labels} 天"
                f"（总标注 {counts['total_labels']} 天{rain_note}），"
                f"暂未启用 ML，03–07 点预测与回测仅使用规则引擎"
            )
        else:
            message = (
                f"有效标注已达标（{eligible} 天），但尚未训练/部署本点位模型；"
                f"03–07 点暂仅使用规则引擎（Admin 重训后可启用 ML）"
            )

    return {
        "ml_active": model_ready and settings.cloudsea_ml_enabled,
        "mode": mode,
        "min_labels": min_labels,
        "eligible_labels": eligible,
        "total_labels": counts["total_labels"],
        "rain_excluded_labels": counts["rain_excluded_labels"],
        "has_spot_model": has_spot_model,
        "message": message,
    }
