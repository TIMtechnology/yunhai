from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.config import settings

W, H = 1200, 630
VW, VH = 900, 1400


def _font(size: int, bold: bool = False):
    candidates = [
        str(Path(settings.share_assets_dir) / "HiraginoSansGB.ttc"),
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/app/data/share-assets/HiraginoSansGB.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _background() -> Image.Image:
    assets = Path(settings.share_assets_dir)
    candidates = sorted(assets.glob("bg_*.png")) if assets.is_dir() else []
    if candidates:
        try:
            img = Image.open(candidates[0]).convert("RGB").resize((W, H))
            overlay = Image.new("RGBA", (W, H), (2, 6, 23, 135))
            return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        except Exception:
            pass
    img = Image.new("RGB", (W, H), "#08111f")
    draw = ImageDraw.Draw(img)
    for y in range(H):
        c = int(18 + 35 * y / H)
        draw.line([(0, y), (W, y)], fill=(6, 20 + c // 3, 38 + c))
    draw.ellipse((-220, 280, 760, 880), fill=(18, 55, 79))
    draw.ellipse((360, 320, 1420, 900), fill=(15, 45, 65))
    draw.rectangle((0, 440, W, H), fill=(9, 22, 38))
    return img


def _vertical_background(height: int = VH) -> Image.Image:
    assets = Path(settings.share_assets_dir)
    candidates = sorted(assets.glob("bg_*.png")) if assets.is_dir() else []
    if candidates:
        try:
            src = Image.open(candidates[0]).convert("RGB")
            scale = max(VW / src.width, height / src.height)
            resized = src.resize((int(src.width * scale), int(src.height * scale)))
            left = max(0, (resized.width - VW) // 2)
            top = max(0, (resized.height - height) // 2)
            img = resized.crop((left, top, left + VW, top + height))
            overlay = Image.new("RGBA", (VW, height), (2, 6, 23, 160))
            return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        except Exception:
            pass
    img = Image.new("RGB", (VW, height), "#08111f")
    draw = ImageDraw.Draw(img)
    for y in range(height):
        c = int(14 + 42 * y / height)
        draw.line([(0, y), (VW, y)], fill=(5, 18 + c // 3, 36 + c))
    draw.ellipse((-260, int(height * 0.52), 620, height + 160), fill=(16, 52, 76))
    draw.ellipse((280, int(height * 0.56), 1220, height + 180), fill=(13, 42, 64))
    return img


def _text(draw: ImageDraw.ImageDraw, xy, text: str, size: int, fill="#e2e8f0", bold=False):
    draw.text(xy, text, font=_font(size, bold=bold), fill=fill)


def _fit_text(text: object, limit: int) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _fmt_pct(value: object) -> str:
    return f"{value}%" if value is not None else "—"


def _fmt_num(value: object, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def _num(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _meteogram_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = ((snapshot.get("meteogram") or {}).get("hours") or [])[:24]
    if rows:
        return rows
    fallback = (snapshot.get("evidence") or {}).get("hourly_evidence_table") or []
    return [
        {
            "time": row.get("time"),
            "temp_c": row.get("temp_c"),
            "rh_pct": row.get("rh_pct"),
            "precip_mm": row.get("precip_mm"),
            "cloud_low": row.get("cloud_low"),
            "cloud_mid": row.get("cloud_mid"),
            "visibility_km": row.get("vis_km"),
            "cloudsea_pct": row.get("cloudsea_pct"),
            "sunrise_pct": row.get("sunrise_pct"),
        }
        for row in fallback
    ]


def _x_at(idx: int, count: int, left: int, right: int) -> float:
    if count <= 1:
        return (left + right) / 2
    return left + (right - left) * idx / (count - 1)


def _value_y(value: float, min_v: float, max_v: float, top: int, bottom: int) -> float:
    if max_v <= min_v:
        return (top + bottom) / 2
    return bottom - (value - min_v) / (max_v - min_v) * (bottom - top)


def _draw_band(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=10, fill=(15, 23, 42, 185), outline=(51, 65, 85, 145), width=1)
    for i in range(1, 4):
        y = y0 + (y1 - y0) * i // 4
        draw.line((x0 + 44, y, x1 - 8, y), fill=(30, 41, 59, 130), width=1)
    _text(draw, (x0 + 10, y0 + 8), label, 15, "#94a3b8")


def _draw_line(
    draw: ImageDraw.ImageDraw,
    values: list[float | None],
    *,
    box: tuple[int, int, int, int],
    color: str,
    width: int = 2,
    min_v: float | None = None,
    max_v: float | None = None,
) -> None:
    valid = [v for v in values if v is not None]
    if not valid:
        return
    x0, y0, x1, y1 = box
    lo = min_v if min_v is not None else min(valid)
    hi = max_v if max_v is not None else max(valid)
    pad = max((hi - lo) * 0.16, 1)
    lo = lo - pad if min_v is None else lo
    hi = hi + pad if max_v is None else hi
    points: list[tuple[float, float]] = []
    for idx, value in enumerate(values):
        if value is None:
            if len(points) > 1:
                draw.line(points, fill=color, width=width, joint="curve")
            points = []
            continue
        points.append((_x_at(idx, len(values), x0, x1), _value_y(value, lo, hi, y0, y1)))
    if len(points) > 1:
        draw.line(points, fill=color, width=width, joint="curve")
    for x, y in points[:: max(1, len(points) // 10)]:
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)


def _cloud_color(value: float | None) -> tuple[int, int, int, int]:
    v = max(0, min(100, value or 0)) / 100
    c = int(25 + 205 * v)
    return (c, c + 6 if c < 240 else 245, 255, int(55 + 170 * v))


def _draw_wind_arrow(draw: ImageDraw.ImageDraw, cx: float, cy: float, deg: object) -> None:
    value = _num(deg)
    if value is None:
        return
    # Open-Meteo gives where wind comes from; share image shows where it blows to.
    to_deg = (value + 180) % 360
    rad = math.radians(to_deg)
    dx = math.sin(rad)
    dy = -math.cos(rad)
    x2, y2 = cx + dx * 9, cy + dy * 9
    x1, y1 = cx - dx * 5, cy - dy * 5
    draw.line((x1, y1, x2, y2), fill="#cbd5e1", width=2)
    wing = math.radians(to_deg + 145)
    draw.line((x2, y2, x2 + math.sin(wing) * 5, y2 - math.cos(wing) * 5), fill="#cbd5e1", width=2)


def _draw_meteogram(
    draw: ImageDraw.ImageDraw,
    rows: list[dict[str, Any]],
    *,
    left: int,
    top: int,
    right: int,
    band_h: int = 96,
    gap: int = 16,
) -> int:
    plot_left, plot_right = left + 58, right - 18
    bands = {
        "temp": (top, top + band_h),
        "rain": (top + (band_h + gap), top + (band_h + gap) + band_h),
        "cloud": (top + 2 * (band_h + gap), top + 2 * (band_h + gap) + 124),
        "wind": (top + 2 * (band_h + gap) + 124 + gap, top + 2 * (band_h + gap) + 124 + gap + 106),
    }
    temp_values = [_num(r.get("temp_c")) for r in rows]
    rh_values = [_num(r.get("rh_pct")) for r in rows]
    precip_values = [_num(r.get("precip_mm")) for r in rows]
    wind_values = [_num(r.get("wind_speed")) for r in rows]
    gust_values = [_num(r.get("wind_gusts")) for r in rows]

    _draw_band(draw, (left, bands["temp"][0], right, bands["temp"][1]), "温度°C")
    _draw_line(draw, temp_values, box=(plot_left, bands["temp"][0] + 16, plot_right, bands["temp"][1] - 14), color="#fb923c", width=3)

    _draw_band(draw, (left, bands["rain"][0], right, bands["rain"][1]), "湿度/降水")
    _draw_line(draw, rh_values, box=(plot_left, bands["rain"][0] + 14, plot_right, bands["rain"][1] - 14), color="#a78bfa", width=2, min_v=0, max_v=100)
    max_precip = max([v for v in precip_values if v is not None] or [1])
    for idx, value in enumerate(precip_values):
        if value is None or value <= 0:
            continue
        x = _x_at(idx, len(rows), plot_left, plot_right)
        y1 = bands["rain"][1] - 14
        y0 = y1 - max(4, value / max(max_precip, 1) * 48)
        draw.rectangle((x - 4, y0, x + 4, y1), fill="#38bdf8")

    _draw_band(draw, (left, bands["cloud"][0], right, bands["cloud"][1]), "云量层")
    cloud_top, cloud_bottom = bands["cloud"][0] + 18, bands["cloud"][1] - 14
    layer_h = (cloud_bottom - cloud_top) / 3
    for layer_idx, (label, key) in enumerate([("高", "cloud_high"), ("中", "cloud_mid"), ("低", "cloud_low")]):
        y0 = cloud_top + layer_idx * layer_h
        _text(draw, (left + 14, int(y0 + 8)), label, 15, "#94a3b8")
        for idx, row in enumerate(rows):
            x0 = _x_at(idx, len(rows), plot_left, plot_right)
            x1 = _x_at(idx + 1, len(rows), plot_left, plot_right) if idx < len(rows) - 1 else plot_right
            draw.rectangle((x0 - 1, y0, x1 + 1, y0 + layer_h - 3), fill=_cloud_color(_num(row.get(key))))

    _draw_band(draw, (left, bands["wind"][0], right, bands["wind"][1]), "风 m/s")
    _draw_line(draw, wind_values, box=(plot_left, bands["wind"][0] + 28, plot_right, bands["wind"][1] - 16), color="#86efac", width=2, min_v=0)
    _draw_line(draw, gust_values, box=(plot_left, bands["wind"][0] + 28, plot_right, bands["wind"][1] - 16), color="#60a5fa", width=2, min_v=0)
    for idx, row in enumerate(rows):
        if idx % 2:
            continue
        _draw_wind_arrow(draw, _x_at(idx, len(rows), plot_left, plot_right), bands["wind"][0] + 18, row.get("wind_direction"))

    for idx, row in enumerate(rows):
        if idx % 3:
            continue
        x = _x_at(idx, len(rows), plot_left, plot_right)
        _text(draw, (int(x - 12), bands["wind"][1] + 8), str(row.get("time") or "")[:2], 14, "#94a3b8")
    return bands["wind"][1] + 36
    wing = math.radians(to_deg - 145)
    draw.line((x2, y2, x2 + math.sin(wing) * 5, y2 - math.cos(wing) * 5), fill="#cbd5e1", width=2)


def render_share_og(snapshot: dict[str, Any]) -> bytes:
    img = _background().convert("RGBA")
    draw = ImageDraw.Draw(img)
    loc = snapshot.get("location") or {}
    target = snapshot.get("target") or {}
    scores = snapshot.get("scores") or {}
    evidence = snapshot.get("evidence") or {}
    pipeline = evidence.get("prediction_pipeline") or {}
    rows = _meteogram_rows(snapshot)
    meta = snapshot.get("forecast_meta") or {}

    draw.rounded_rectangle((42, 38, 1158, 588), radius=28, fill=(15, 23, 42, 222), outline=(125, 211, 252, 135), width=2)

    _text(draw, (74, 68), "气象详图 · 日出云海预测", 29, "#7dd3fc", True)
    _text(draw, (74, 112), _fit_text(loc.get("display_name") or "云海日出点位", 18), 38, "#f8fafc", True)
    _text(draw, (74, 164), f"{target.get('date', '')} · {target.get('weekday') or ''} · 日出 {target.get('sunrise_time') or '—'}", 22, "#cbd5e1")

    verdict = scores.get("verdict") or "预测参考"
    cards = [
        ("云海", scores.get("cloudsea_prob_pct"), scores.get("cloudsea_grade"), "#38bdf8"),
        ("日出", scores.get("sunrise_prob_pct"), scores.get("sunrise_grade"), "#fb923c"),
        ("综合", scores.get("combined_score"), "score", "#86efac"),
    ]
    for i, (label, val, grade, color) in enumerate(cards):
        x = 620 + i * 168
        draw.rounded_rectangle((x, 70, x + 144, 142), radius=16, fill=(30, 41, 59, 225))
        _text(draw, (x + 16, 80), label, 18, "#94a3b8")
        suffix = "%" if label != "综合" else ""
        _text(draw, (x + 16, 104), f"{val if val is not None else '—'}{suffix}", 29, color, True)
        _text(draw, (x + 86, 112), str(grade or ""), 15, "#cbd5e1")

    _text(draw, (620, 154), f"{verdict} · {scores.get('scenario_label') or '—'}", 22, "#fcd34d", True)

    plot_left, plot_right = 112, 1118
    bands = {
        "temp": (214, 269),
        "rain": (278, 333),
        "cloud": (342, 417),
        "wind": (426, 486),
    }
    temp_values = [_num(r.get("temp_c")) for r in rows]
    rh_values = [_num(r.get("rh_pct")) for r in rows]
    precip_values = [_num(r.get("precip_mm")) for r in rows]
    wind_values = [_num(r.get("wind_speed")) for r in rows]
    gust_values = [_num(r.get("wind_gusts")) for r in rows]

    _draw_band(draw, (72, bands["temp"][0], 1134, bands["temp"][1]), "温度°C")
    _draw_line(draw, temp_values, box=(plot_left, bands["temp"][0] + 8, plot_right, bands["temp"][1] - 8), color="#fb923c", width=3)

    _draw_band(draw, (72, bands["rain"][0], 1134, bands["rain"][1]), "湿度/降水")
    _draw_line(draw, rh_values, box=(plot_left, bands["rain"][0] + 8, plot_right, bands["rain"][1] - 8), color="#a78bfa", width=2, min_v=0, max_v=100)
    max_precip = max([v for v in precip_values if v is not None] or [1])
    for idx, value in enumerate(precip_values):
        if value is None or value <= 0:
            continue
        x = _x_at(idx, len(rows), plot_left, plot_right)
        y1 = bands["rain"][1] - 8
        y0 = y1 - max(3, value / max(max_precip, 1) * 34)
        draw.rectangle((x - 3, y0, x + 3, y1), fill="#38bdf8")

    _draw_band(draw, (72, bands["cloud"][0], 1134, bands["cloud"][1]), "云量层")
    cloud_top, cloud_bottom = bands["cloud"][0] + 10, bands["cloud"][1] - 10
    layer_h = (cloud_bottom - cloud_top) / 3
    layer_keys = [("高", "cloud_high"), ("中", "cloud_mid"), ("低", "cloud_low")]
    for layer_idx, (label, key) in enumerate(layer_keys):
        y0 = cloud_top + layer_idx * layer_h
        _text(draw, (86, int(y0 + 4)), label, 13, "#94a3b8")
        for idx, row in enumerate(rows):
            x0 = _x_at(idx, len(rows), plot_left, plot_right)
            x1 = _x_at(idx + 1, len(rows), plot_left, plot_right) if idx < len(rows) - 1 else plot_right
            draw.rectangle((x0 - 1, y0, x1 + 1, y0 + layer_h - 2), fill=_cloud_color(_num(row.get(key))))

    _draw_band(draw, (72, bands["wind"][0], 1134, bands["wind"][1]), "风 m/s")
    _draw_line(draw, wind_values, box=(plot_left, bands["wind"][0] + 10, plot_right, bands["wind"][1] - 12), color="#86efac", width=2, min_v=0)
    _draw_line(draw, gust_values, box=(plot_left, bands["wind"][0] + 10, plot_right, bands["wind"][1] - 12), color="#60a5fa", width=2, min_v=0)
    for idx, row in enumerate(rows):
        if idx % 2:
            continue
        _draw_wind_arrow(draw, _x_at(idx, len(rows), plot_left, plot_right), bands["wind"][0] + 11, row.get("wind_direction"))

    label_count = len(rows)
    for idx, row in enumerate(rows):
        if idx % 3:
            continue
        x = _x_at(idx, label_count, plot_left, plot_right)
        _text(draw, (int(x - 12), 492), str(row.get("time") or "")[:2], 13, "#94a3b8")

    model_cards = [
        ("规则", _fmt_pct(pipeline.get("rule_engine_cloudsea_pct")), "#93c5fd"),
        ("ML", _fmt_pct(pipeline.get("ml_raw_cloudsea_pct")) if pipeline.get("ml_active") else "未启用", "#c4b5fd"),
        ("融合", _fmt_pct(pipeline.get("fused_display_cloudsea_pct")), "#67e8f9"),
    ]
    _text(draw, (74, 518), "模型对照", 20, "#bae6fd", True)
    for i, (label, value, color) in enumerate(model_cards):
        x = 176 + i * 150
        draw.rounded_rectangle((x, 512, x + 126, 552), radius=12, fill=(8, 47, 73, 180))
        _text(draw, (x + 12, 522), label, 16, "#94a3b8")
        _text(draw, (x + 58, 518), value, 22, color, True)

    factor_snippets = pipeline.get("factor_snippets") or []
    important = []
    for factor in factor_snippets:
        if factor.get("label") in {"云海型态", "分层云量", "能见度观测"} and factor.get("value") is not None:
            important.append(f"{factor.get('label')}：{factor.get('value')}")
        if len(important) >= 2:
            break
    if important:
        _text(draw, (660, 522), _fit_text(" · ".join(important), 28), 16, "#dbeafe")

    source = meta.get("source") or "open-meteo"
    model = meta.get("model") or "forecast"
    _text(draw, (74, 562), "yunhai.timkj.com", 18, "#94a3b8")
    _text(draw, (920, 562), f"数据源 {source} · {model}", 17, "#94a3b8")

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_share_image(snapshot: dict[str, Any]) -> bytes:
    loc = snapshot.get("location") or {}
    target = snapshot.get("target") or {}
    scores = snapshot.get("scores") or {}
    evidence = snapshot.get("evidence") or {}
    pipeline = evidence.get("prediction_pipeline") or {}
    rows = _meteogram_rows(snapshot)
    meta = snapshot.get("forecast_meta") or {}
    factor_snippets = pipeline.get("factor_snippets") or []
    details: list[str] = []
    for factor in factor_snippets:
        label = factor.get("label")
        value = factor.get("value")
        if label in {"ML 云海模型", "规则引擎参考", "云海型态", "分层云量", "能见度观测"} and value is not None:
            details.append(f"{label}：{value}")
        if len(details) >= 6:
            break
    sunrise_rows = [r for r in rows if str(r.get("time") or "")[:2] in {"03", "04", "05", "06"}] or rows[:4]

    chart_top = 532
    chart_bottom_est = chart_top + 112 + 18 + 112 + 18 + 124 + 18 + 106 + 36
    model_top = chart_bottom_est + 58
    detail_top = model_top + 150
    table_top = detail_top + 52 + max(1, len(details)) * 38 + 26
    footer_y = table_top + 54 + max(1, len(sunrise_rows)) * 62 + 86
    height = max(VH, footer_y + 82)

    img = _vertical_background(height).convert("RGBA")
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((46, 48, 854, height - 64), radius=34, fill=(15, 23, 42, 226), outline=(125, 211, 252, 145), width=2)

    _text(draw, (82, 88), "气象详图 · 日出云海预测", 34, "#7dd3fc", True)
    _text(draw, (82, 144), _fit_text(loc.get("display_name") or "云海日出点位", 16), 48, "#f8fafc", True)
    _text(draw, (82, 210), f"{target.get('date', '')} · {target.get('weekday') or ''} · 日出 {target.get('sunrise_time') or '—'}", 27, "#cbd5e1")

    verdict = scores.get("verdict") or "预测参考"
    _text(draw, (82, 270), f"{verdict} · {scores.get('scenario_label') or '—'}", 32, "#fcd34d", True)

    cards = [
        ("云海", scores.get("cloudsea_prob_pct"), scores.get("cloudsea_grade"), "#38bdf8"),
        ("日出", scores.get("sunrise_prob_pct"), scores.get("sunrise_grade"), "#fb923c"),
        ("综合", scores.get("combined_score"), "score", "#86efac"),
    ]
    for i, (label, val, grade, color) in enumerate(cards):
        x = 82 + i * 258
        draw.rounded_rectangle((x, 330, x + 222, 432), radius=22, fill=(30, 41, 59, 226))
        _text(draw, (x + 22, 346), label, 24, "#94a3b8")
        suffix = "%" if label != "综合" else ""
        _text(draw, (x + 22, 382), f"{val if val is not None else '—'}{suffix}", 38, color, True)
        _text(draw, (x + 128, 394), str(grade or ""), 18, "#cbd5e1")

    _text(draw, (82, 482), "24小时气象详图", 28, "#bae6fd", True)
    chart_bottom = _draw_meteogram(draw, rows, left=82, top=chart_top, right=818, band_h=112, gap=18)
    _text(draw, (82, chart_bottom + 8), "图例：橙=温度，紫=湿度，蓝柱=降水，云量层为高/中/低云，绿=风速，蓝=阵风，箭头=风吹向", 18, "#94a3b8")

    model_top = chart_bottom + 58
    _text(draw, (82, model_top), "预测模型对照", 28, "#bae6fd", True)
    model_cards = [
        ("规则引擎", _fmt_pct(pipeline.get("rule_engine_cloudsea_pct")), "#93c5fd"),
        ("ML模型", _fmt_pct(pipeline.get("ml_raw_cloudsea_pct")) if pipeline.get("ml_active") else "未启用", "#c4b5fd"),
        ("融合展示", _fmt_pct(pipeline.get("fused_display_cloudsea_pct")), "#67e8f9"),
    ]
    for i, (label, value, color) in enumerate(model_cards):
        x = 82 + i * 248
        draw.rounded_rectangle((x, model_top + 46, x + 216, model_top + 118), radius=18, fill=(8, 47, 73, 185))
        _text(draw, (x + 18, model_top + 62), label, 21, "#94a3b8")
        _text(draw, (x + 112, model_top + 58), value, 31, color, True)

    detail_top = model_top + 150
    _text(draw, (82, detail_top), "系统关键数据", 28, "#fcd34d", True)
    y = detail_top + 46
    for item in details:
        _text(draw, (98, y), "•", 24, "#38bdf8", True)
        _text(draw, (126, y + 2), _fit_text(item, 34), 22, "#dbeafe")
        y += 38

    table_top = y + 28
    _text(draw, (82, table_top), "日出窗口逐时数据", 28, "#bae6fd", True)
    y = table_top + 48
    for row in sunrise_rows:
        draw.rounded_rectangle((82, y - 8, 818, y + 48), radius=12, fill=(30, 41, 59, 145))
        _text(draw, (100, y), str(row.get("time") or "--"), 19, "#f8fafc", True)
        _text(draw, (190, y), f"云海 {_fmt_pct(row.get('cloudsea_pct'))}", 18, "#38bdf8")
        _text(draw, (330, y), f"日出 {_fmt_pct(row.get('sunrise_pct'))}", 18, "#fb923c")
        _text(draw, (470, y), f"温湿 {_fmt_num(row.get('temp_c'), '°')} / RH{_fmt_pct(row.get('rh_pct'))}", 18, "#e0f2fe")
        _text(draw, (100, y + 26), f"低/中云 {_fmt_pct(row.get('cloud_low'))}/{_fmt_pct(row.get('cloud_mid'))}", 17, "#bae6fd")
        _text(draw, (330, y + 26), f"能见 {_fmt_num(row.get('visibility_km'), 'km')}", 17, "#dbeafe")
        _text(draw, (520, y + 26), f"降水 {_fmt_num(row.get('precip_mm'), 'mm')}", 17, "#dbeafe")
        y += 62

    if rows:
        best_vis = min((_num(r.get("visibility_km")) for r in sunrise_rows if _num(r.get("visibility_km")) is not None), default=None)
        max_cloudsea = max((_num(r.get("cloudsea_pct")) for r in sunrise_rows if _num(r.get("cloudsea_pct")) is not None), default=None)
        y += 20
        _text(draw, (82, y), f"日出窗口：云海峰值 {_fmt_pct(int(max_cloudsea) if max_cloudsea is not None else None)} · 最低能见度 {_fmt_num(best_vis, 'km')}", 22, "#e0f2fe")
        y += 42

    source = meta.get("source") or "open-meteo"
    model = meta.get("model") or "forecast"
    footer = max(y + 18, height - 116)
    _text(draw, (82, footer), "yunhai.timkj.com", 20, "#94a3b8")
    _text(draw, (570, footer), f"数据源 {source} · {model}", 19, "#94a3b8")

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
