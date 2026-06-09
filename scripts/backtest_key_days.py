#!/usr/bin/env python3
"""关键标注日回测：支持当日气象缓存与提前一日预报两种模式。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.prod.db"))
_pre_args, _ = _pre.parse_known_args()
os.environ["CLOUDSEA_DB_PATH"] = str(Path(_pre_args.db).resolve())
os.environ.setdefault("CLOUDSEA_ML_ENABLED", "1")

from app.adapters.open_meteo_historical import (  # noqa: E402
    fetch_historical_forecast,
    parse_astronomy_for_date,
    slice_hourly_for_date,
)
from app.models.schemas import PredictRequest  # noqa: E402
from app.services.cloudsea_store import init_store  # noqa: E402
from app.services.predictor import build_predictions_from_hourly, run_backtest_prediction  # noqa: E402
from app.services.spot_loader import get_spot, get_viewpoint  # noqa: E402
from app.adapters.dem import get_terrain_context  # noqa: E402
from app.engine.viewing_mode import resolve_viewing_mode  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


def load_label(db_path: Path, spot_id: str, viewpoint_id: str, date_key: str) -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT * FROM cloudsea_labels
        WHERE spot_id=? AND viewpoint_id=? AND date=?
        """,
        (spot_id, viewpoint_id, date_key),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def label_bucket(status: str) -> str:
    return "有云海" if status in ("full", "partial") else "无云海"


async def backtest_advance_forecast(
    req: PredictRequest,
    target: date,
    *,
    cloudsea_months,
    sunrise_months,
) -> dict:
    """用 target-1 日可获得的 historical forecast 预测 target 日（模拟提前报）。"""
    issued = target - timedelta(days=1)
    forecast = await fetch_historical_forecast(req.lat, req.lng, issued, target)
    hourly = slice_hourly_for_date(forecast.get("hourly", {}), target)
    astro = parse_astronomy_for_date(forecast, target)
    astronomy = {target.isoformat(): astro} if astro else {}
    terrain = await get_terrain_context(
        req.lat,
        req.lng,
        elevation=req.elevation or 804,
        profile_date=target,
        spot_id=req.spot_id,
        viewpoint_id=req.viewpoint_id,
    )
    viewing_mode, viewing_mode_note, _ = resolve_viewing_mode(
        spot_id=req.spot_id,
        viewpoint_id=req.viewpoint_id,
        elevation=req.elevation or 804,
        terrain=terrain,
    )
    terrain["viewing_mode"] = viewing_mode
    now = datetime(target.year, target.month, target.day, 12, 0, tzinfo=TZ)
    results = build_predictions_from_hourly(
        req=req,
        elevation=req.elevation or 804,
        hourly=hourly,
        astronomy=astronomy,
        cloudsea_months=cloudsea_months,
        sunrise_months=sunrise_months,
        satellite_context=None,
        now=now,
        terrain=terrain,
        viewing_mode=viewing_mode,
    )
    window = [h for h in results if 3 <= datetime.fromisoformat(h.time).hour < 7]
    peak = max(window, key=lambda h: h.cloudsea.probability) if window else None
    sunrise = next((h for h in results if h.is_sunrise_window), None)
    day_row = next((d for d in results if d.time.startswith(target.isoformat())), None)
    def _factor_value(factors, key):
        detail = factors.get(key) if factors else None
        if detail is None:
            return None
        return getattr(detail, "value", None) if not isinstance(detail, dict) else detail.get("value")

    return {
        "data_source": f"advance_forecast_from_{issued.isoformat()}",
        "peak_prob": peak.cloudsea.probability if peak else 0,
        "peak_time": peak.time if peak else None,
        "scenario": sunrise.scenario.label if sunrise else None,
        "rule_at_peak": _factor_value(peak.cloudsea.factors, "fuzzy_reference") if peak else None,
        "ml_at_peak": _factor_value(peak.cloudsea.factors, "ml_model") if peak else None,
    }


async def backtest_cached(req: PredictRequest, target: date) -> dict:
    bt = await run_backtest_prediction(req=req, target_date=target, prefer_cached_meteo=True)
    summary = bt.get("sunrise_window_summary") or {}
    meta = bt.get("meta") or {}
    peak = summary.get("max_cloudsea_prob") or 0
    pred = bt.get("prediction") or {}
    days = pred.get("days") or []
    day = next(
        (d for d in days if (d.get("date") if isinstance(d, dict) else d.date) == target.isoformat()),
        None,
    )
    return {
        "data_source": meta.get("data_source") or "cached",
        "peak_prob": int(peak),
        "peak_time": summary.get("peak_time"),
        "scenario": (
            day.get("sunrise_scenario_label")
            if isinstance(day, dict)
            else (day.sunrise_scenario_label if day else summary.get("scenario"))
        ),
        "sunrise_combined": (
            day.get("sunrise_combined_score")
            if isinstance(day, dict)
            else (day.sunrise_combined_score if day else None)
        ),
    }


async def run_case(
    db_path: Path,
    spot_id: str,
    viewpoint_id: str,
    date_key: str,
    *,
    modes: list[str],
) -> dict:
    label = load_label(db_path, spot_id, viewpoint_id, date_key)
    vp = get_viewpoint(spot_id, viewpoint_id)
    spot = get_spot(spot_id)
    req = PredictRequest(
        lat=vp.lat,
        lng=vp.lng,
        elevation=vp.elevation,
        name=f"{spot.name} · {vp.name}",
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        hours=24,
    )
    out: dict = {
        "date": date_key,
        "label": label["status"] if label else "?",
        "label_bucket": label_bucket(label["status"]) if label else "?",
    }
    if "cached" in modes:
        out["cached"] = await backtest_cached(req, date.fromisoformat(date_key))
    if "advance" in modes:
        out["advance"] = await backtest_advance_forecast(
            req,
            date.fromisoformat(date_key),
            cloudsea_months=spot.seasonality.get("cloudsea_months"),
            sunrise_months=spot.seasonality.get("sunrise_months"),
        )
    cached_peak = out.get("cached", {}).get("peak_prob", 0)
    advance_peak = out.get("advance", {}).get("peak_prob", 0)
    threshold = 55
    out["cached_match"] = (cached_peak >= threshold) == (out["label_bucket"] == "有云海")
    if "advance" in out:
        out["advance_match"] = (advance_peak >= threshold) == (out["label_bucket"] == "有云海")
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.prod.db"))
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument(
        "--modes",
        nargs="+",
        default=["cached", "advance"],
        choices=["cached", "advance"],
    )
    parser.add_argument("--json-out")
    args = parser.parse_args()

    db_path = Path(args.db)
    init_store()

    results = []
    for d in args.dates:
        results.append(
            await run_case(
                db_path,
                args.spot_id,
                args.viewpoint_id,
                d,
                modes=args.modes,
            )
        )

    print(f"\n=== 回测 {args.spot_id}/{args.viewpoint_id} n={len(results)} ===\n")
    for r in results:
        mark_c = "✓" if r.get("cached_match") else "✗"
        line = (
            f"{r['date']} 标注={r['label_bucket']:4} ({r['label']})"
        )
        if "cached" in r:
            c = r["cached"]
            line += (
                f"\n  [{mark_c} 缓存/当日] 峰值={c['peak_prob']:3}% 场景={c.get('scenario')} "
                f"源={c['data_source']}"
            )
        if "advance" in r:
            mark_a = "✓" if r.get("advance_match") else "✗"
            a = r["advance"]
            line += (
                f"\n  [{mark_a} 提前报] 峰值={a['peak_prob']:3}% 场景={a.get('scenario')} "
                f"规则={a.get('rule_at_peak')} ML={a.get('ml_at_peak')} 源={a['data_source']}"
            )
        print(line + "\n")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {args.json_out}")


if __name__ == "__main__":
    asyncio.run(main())
