from __future__ import annotations

import io
import statistics
from typing import Optional

from app.models.schemas import FactorDetail


def analyze_ir_image(content: bytes) -> dict:
    """从 Himawari 红外 JPEG 提取区域云量特征。"""
    from PIL import Image

    img = Image.open(io.BytesIO(content)).convert("L")
    pixels = list(img.getdata())
    if not pixels:
        return _empty_analysis()

    mean = statistics.mean(pixels)
    stdev = statistics.pstdev(pixels) if len(pixels) > 1 else 0.0

    # 灰度分布：均值为基线，高于基线且有一定起伏视为云区
    threshold = mean + max(6.0, stdev * 0.25)
    cloud_pixels = sum(1 for p in pixels if p >= threshold)
    cloud_fraction = cloud_pixels / len(pixels) * 100

    # 纹理：标准差高说明有云块结构
    structured = stdev >= 12.0
    uniformity = max(0.0, 1.0 - min(stdev / 40.0, 1.0))

    return {
        "cloud_fraction": round(cloud_fraction, 1),
        "ir_mean": round(mean, 1),
        "ir_std": round(stdev, 1),
        "structured": structured,
        "uniformity": round(uniformity, 2),
    }


def _empty_analysis() -> dict:
    return {
        "cloud_fraction": 0.0,
        "ir_mean": 0.0,
        "ir_std": 0.0,
        "structured": False,
        "uniformity": 0.0,
    }


def build_satellite_factor(
    satellite_ctx: Optional[dict],
    meteo_cloud_total: float,
) -> tuple[float, Optional[FactorDetail]]:
    """对比卫星区域云量与单点预报，给出评分修正。"""
    if not satellite_ctx:
        return 0.0, None

    sat_frac = float(satellite_ctx.get("cloud_fraction") or 0)
    delta = sat_frac - meteo_cloud_total
    structured = bool(satellite_ctx.get("structured"))

    # 卫星看到比单点预报更多云 → 上调；明显更少 → 略降
    base = delta / 100.0 * 0.4
    if structured and sat_frac >= 35:
        base += 0.08
    if sat_frac >= 55:
        base += 0.05

    adjustment = max(-0.12, min(0.25, base))
    score = max(0.0, min(1.0, 0.55 + adjustment * 2))

    lookback = int(satellite_ctx.get("lookback_hours") or 0)
    utc = satellite_ctx.get("datetime_utc") or ""
    time_note = f"UTC {utc}" + (f"（回溯{lookback}h）" if lookback else "")

    factor = FactorDetail(
        score=round(score, 3),
        weight=0.12,
        label="卫星区域云量",
        description="Himawari 红外裁切与 Open-Meteo 单点云量交叉验证",
        value=f"卫星≈{sat_frac:.0f}% · 预报≈{meteo_cloud_total:.0f}% · {time_note}",
    )
    return adjustment, factor
