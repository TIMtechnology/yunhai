"""太阳位置：日出方位角（用于峰顶可观测场扇区）。"""

from __future__ import annotations

import math
from datetime import date as date_cls, datetime, timezone


def _day_of_year(d: date_cls) -> int:
    return int(d.strftime("%j"))


def sunrise_azimuth_deg(lat_deg: float, lng_deg: float, day: date_cls) -> float:
    """日出时刻太阳方位角（正北为 0°，顺时针，度）。"""
    lat = math.radians(lat_deg)
    n = _day_of_year(day)
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + n) / 365.0)))
    hour_angle = math.acos(max(-1.0, min(1.0, -math.tan(lat) * math.tan(decl))))
    az = math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat),
    )
    deg = math.degrees(az)
    return (deg + 360.0) % 360.0


def sunrise_azimuth_for_datetime(lat_deg: float, lng_deg: float, dt: datetime) -> float:
    d = dt.astimezone(timezone.utc).date()
    return sunrise_azimuth_deg(lat_deg, lng_deg, d)
