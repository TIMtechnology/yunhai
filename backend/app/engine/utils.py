from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def parse_shanghai_time(value: str) -> datetime:
    """Open-Meteo 在 timezone=Asia/Shanghai 时返回无时区后缀的本地时刻。"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI_TZ)
    return dt.astimezone(SHANGHAI_TZ)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def grade_from_probability(probability: int) -> str:
    if probability >= 80:
        return "极佳"
    if probability >= 60:
        return "良好"
    if probability >= 40:
        return "一般"
    if probability >= 20:
        return "较差"
    return "不宜"


def bell_score(value: float, ideal: float, width: float) -> float:
    distance = abs(value - ideal)
    return clamp(1.0 - distance / width)


def range_score(value: float, low: float, high: float) -> float:
    if low <= value <= high:
        return 1.0
    if value < low:
        return clamp(1.0 - (low - value) / max(low, 1))
    return clamp(1.0 - (value - high) / max(100 - high, 1))
