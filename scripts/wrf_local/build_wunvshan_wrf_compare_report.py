#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.schemas import PredictRequest
from app.services.meteo_profile import build_meteo_profile
from app.services.predictor import run_prediction


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_ROOT = ROOT / "data" / "wrf-local"
DEFAULT_REPORT_DIR = ROOT / "data" / "cloudsea" / "reports"
WUNVSHAN = {
    "spot_id": "wunvshan",
    "viewpoint_id": "dianjiangtai",
    "name": "本溪五女山 · 点将台",
    "lat": 41.31976,
    "lng": 125.40773,
    "elevation": 804,
}


def _fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{unit}"
    return html.escape(str(value))


def _score_summary(payload: dict[str, Any]) -> dict[str, Any]:
    days = payload.get("days") or []
    hours = payload.get("hours") or []
    first_day = days[0] if days else {}
    sunrise_time = first_day.get("sunrise_time")
    sunrise_peak = first_day.get("sunrise_window_peak_cloudsea_prob")
    full_peak = first_day.get("full_day_peak_cloudsea_prob") or first_day.get("peak_cloudsea_prob")
    sunrise_combined = first_day.get("sunrise_combined_score")
    best = None
    if hours:
        best = max(hours, key=lambda item: int((item.get("cloudsea") or {}).get("probability") or 0))
    return {
        "date": first_day.get("date"),
        "sunrise_time": sunrise_time,
        "sunrise_window_peak_cloudsea_prob": sunrise_peak,
        "full_day_peak_cloudsea_prob": full_peak,
        "sunrise_combined_score": sunrise_combined,
        "best_hour": best,
        "ml_status": (payload.get("location") or {}).get("ml_status") or {},
    }


def _hour_rows(payload: dict[str, Any], limit: int = 30) -> str:
    rows: list[str] = []
    for item in (payload.get("hours") or [])[:limit]:
        weather = item.get("weather") or {}
        cloudsea = item.get("cloudsea") or {}
        sunrise = item.get("sunrise") or {}
        scenario = item.get("scenario") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('time', '-')))}</td>"
            f"<td>{_fmt(cloudsea.get('probability'), '%', 0)} / {_fmt(sunrise.get('probability'), '%', 0)}</td>"
            f"<td>{_fmt(weather.get('temperature'), '°C')}</td>"
            f"<td>{_fmt(weather.get('humidity'), '%', 0)}</td>"
            f"<td>{_fmt(weather.get('cloud_cover_low'), '%', 0)} / {_fmt(weather.get('cloud_cover_mid'), '%', 0)} / {_fmt(weather.get('cloud_cover_high'), '%', 0)}</td>"
            f"<td>{_fmt(weather.get('wind_speed'), 'm/s')} · {_fmt(weather.get('wind_direction'), '°', 0)}</td>"
            f"<td>{_fmt(weather.get('visibility'), 'km')}</td>"
            f"<td>{html.escape(str(scenario.get('label') or '-'))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _profile_rows(profile: dict[str, Any], limit: int = 12) -> str:
    rows: list[str] = []
    for hour in (profile.get("hours") or [])[:limit]:
        levels = hour.get("levels") or []
        low = [x for x in levels if float(x.get("height_m_asl") or 0) <= 2500]
        mid = [x for x in levels if 2500 < float(x.get("height_m_asl") or 0) <= 6000]
        low_cloud = max((x.get("cloud_cover_pct") or 0 for x in low), default=None)
        mid_cloud = max((x.get("cloud_cover_pct") or 0 for x in mid), default=None)
        low_rh = max((x.get("rh_pct") or 0 for x in low), default=None)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(hour.get('time', '-')))}</td>"
            f"<td>{_fmt(hour.get('cloud_base_estimate_m'), 'm', 0)}</td>"
            f"<td>{_fmt(low_cloud, '%', 0)}</td>"
            f"<td>{_fmt(mid_cloud, '%', 0)}</td>"
            f"<td>{_fmt(low_rh, '%', 0)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _factor_rows(payload: dict[str, Any]) -> str:
    best = _score_summary(payload).get("best_hour") or {}
    factors = ((best.get("cloudsea") or {}).get("factors") or {})
    rows: list[str] = []
    for key, factor in sorted(factors.items(), key=lambda kv: float(kv[1].get("weight") or 0), reverse=True)[:12]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(key))}</td>"
            f"<td>{html.escape(str(factor.get('label') or '-'))}</td>"
            f"<td>{_fmt(factor.get('score'))}</td>"
            f"<td>{_fmt(factor.get('weight'))}</td>"
            f"<td>{html.escape(str(factor.get('value') or '-'))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _wrf_rows(wrf: dict[str, Any], limit: int = 30) -> str:
    rows: list[str] = []
    for item in (wrf.get("evidence") or {}).get("hourly", [])[:limit]:
        cloud = item.get("cloud_layers") or {}
        boundary = item.get("cloud_boundary") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('time', '-')))}</td>"
            f"<td>{_fmt(item.get('temperature_2m_c'), '°C')}</td>"
            f"<td>{_fmt(item.get('relative_humidity_2m'), '%', 0)}</td>"
            f"<td>{_fmt(cloud.get('low'), '%', 0)} / {_fmt(cloud.get('mid'), '%', 0)} / {_fmt(cloud.get('high'), '%', 0)}</td>"
            f"<td>{_fmt(boundary.get('base_m_agl'), 'm', 0)} / {_fmt(boundary.get('top_m_agl'), 'm', 0)}</td>"
            f"<td>{_fmt(item.get('low_level_inversion_c'), '°C')}</td>"
            f"<td>{_fmt(item.get('wind_speed_10m'), 'm/s')} · {_fmt(item.get('wind_direction_10m'), '°', 0)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _wrf_readiness(work_root: Path) -> dict[str, Any]:
    expected = ["geogrid.exe", "ungrib.exe", "metgrid.exe", "real.exe", "wrf.exe"]
    fixed = work_root / "fixed"
    gfs = work_root / "cache" / "gfs"
    run = work_root / "runs" / "wunvshan"
    products = work_root / "products" / "wunvshan"
    evidence_files = sorted(products.glob("*.cloudsea-evidence.json")) if products.exists() else []
    evidence = json.loads(evidence_files[-1].read_text(encoding="utf-8")) if evidence_files else None
    missing = [name for name in expected if not (fixed / "bin" / name).exists()]
    return {
        "executables_ready": not missing,
        "missing_executables": missing,
        "fixed_dir": str(fixed),
        "fixed_dir_exists": fixed.exists(),
        "gfs_cache_dir": str(gfs),
        "gfs_cycles": [p.name for p in gfs.iterdir()] if gfs.exists() else [],
        "prepared_runs": [p.name for p in run.iterdir()] if run.exists() else [],
        "evidence_path": str(evidence_files[-1]) if evidence_files else None,
        "evidence": evidence,
        "can_measure_wrf_lift": evidence is not None,
        "reason": "WRF 已完成本地真实运行并生成 wrfout 证据。" if evidence else "WRF 可执行文件和缓存已准备，但尚未生成 evidence JSON。",
    }


async def _predict(ml_enabled: bool, hours: int) -> dict[str, Any]:
    settings.cloudsea_ml_enabled = ml_enabled
    req = PredictRequest(
        lat=WUNVSHAN["lat"],
        lng=WUNVSHAN["lng"],
        elevation=WUNVSHAN["elevation"],
        name=WUNVSHAN["name"],
        spot_id=WUNVSHAN["spot_id"],
        viewpoint_id=WUNVSHAN["viewpoint_id"],
        hours=hours,
    )
    result = await run_prediction(req)
    return result.model_dump(mode="json")


def _html_report(rule: dict[str, Any], ml: dict[str, Any], profile: dict[str, Any], wrf: dict[str, Any]) -> str:
    rule_summary = _score_summary(rule)
    ml_summary = _score_summary(ml)
    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    rule_peak = rule_summary.get("sunrise_window_peak_cloudsea_prob")
    ml_peak = ml_summary.get("sunrise_window_peak_cloudsea_prob")
    delta = (ml_peak - rule_peak) if isinstance(rule_peak, int) and isinstance(ml_peak, int) else None
    verdict = (
        f"本次 ML 融合相对规则基线变化 {_fmt(delta, 'pct', 0)}；"
        + ("WRF 已产出真实 wrfout，可用于佐证低云、云底、湿度、风场和逆温。" if wrf.get("can_measure_wrf_lift") else "WRF 尚未产出真实 wrfout，不能实测提升。")
        if delta is not None
        else "规则与 ML 峰值缺少可比数据；" + ("WRF 已产出真实 wrfout，可查看下方证据。" if wrf.get("can_measure_wrf_lift") else "WRF 尚未产出真实 wrfout，不能实测提升。")
    )
    wrf_status_class = "ok" if wrf.get("can_measure_wrf_lift") else "warn"
    wrf_status_label = "已实测" if wrf.get("can_measure_wrf_lift") else "未实测"
    missing_executables = ", ".join(wrf["missing_executables"]) or "无"
    missing_class = "warn" if wrf.get("missing_executables") else "ok"
    evidence_path = str(wrf.get("evidence_path") or "-")
    nearest_grid = str(((wrf.get("evidence") or {}).get("nearest_grid") or {}))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>五女山 Open-Meteo + ML + WRF 对比报告</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f7fb; color: #172033; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 56px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; }}
    p {{ color: #42526b; line-height: 1.7; }}
    section {{ background: #fff; border: 1px solid #e5eaf3; border-radius: 18px; padding: 20px; margin-top: 18px; box-shadow: 0 16px 40px rgba(34, 54, 84, .08); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid #edf1f7; text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #f8fafc; color: #5a6b85; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #edf1f7; border-radius: 14px; padding: 14px; background: #fbfdff; }}
    .card b {{ display: block; margin-top: 6px; font-size: 24px; }}
    .warn {{ border-color: #ffd6a8; background: #fff8ef; }}
    .ok {{ border-color: #bae6c7; background: #f1fff5; }}
    .table-wrap {{ overflow-x: auto; }}
    .muted {{ color: #718096; font-size: 13px; }}
    code {{ background: #eef2f7; padding: 2px 6px; border-radius: 6px; }}
  </style>
</head>
<body>
<main>
  <h1>五女山 Open-Meteo + ML + WRF 对比报告</h1>
  <p>点位：本溪五女山 · 点将台。该报告用于验证现有 Open-Meteo/规则/ML 基线，并检查 WRF 第一阶段运行条件。</p>
  <p class="muted">生成时间：{generated_at}</p>

  <section>
    <h2>一句话结论</h2>
    <p>{html.escape(verdict)}</p>
  </section>

  <section>
    <h2>核心分数对比</h2>
    <div class="grid">
      <div class="card">
        规则基线 · 日出窗口云海峰值
        <b>{_fmt(rule_summary.get("sunrise_window_peak_cloudsea_prob"), '%', 0)}</b>
        <span>全天峰值 {_fmt(rule_summary.get("full_day_peak_cloudsea_prob"), '%', 0)}</span>
      </div>
      <div class="card">
        ML 融合 · 日出窗口云海峰值
        <b>{_fmt(ml_summary.get("sunrise_window_peak_cloudsea_prob"), '%', 0)}</b>
        <span>相对规则 {_fmt(delta, 'pct', 0)}</span>
      </div>
      <div class="card">
        ML 状态
        <b>{html.escape(str((ml_summary.get("ml_status") or {}).get("ml_active")))}</b>
        <span>{html.escape(str((ml_summary.get("ml_status") or {}).get("message") or "-"))}</span>
      </div>
      <div class="card {wrf_status_class}">
        WRF 状态
        <b>{wrf_status_label}</b>
        <span>{html.escape(wrf["reason"])}</span>
      </div>
    </div>
  </section>

  <section>
    <h2>Open-Meteo 小时预测（ML 融合）</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>云海/日出</th><th>温度</th><th>湿度</th><th>低/中/高云</th><th>风</th><th>能见度</th><th>场景</th></tr></thead>
        <tbody>{_hour_rows(ml)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Open-Meteo 垂直廓线证据</h2>
    <p>这是当前 Meteogram 使用的压力层资料，可作为未来 WRF wrfout 对比的基线。</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>估算云底</th><th>低层最大云量</th><th>中层最大云量</th><th>低层最大湿度</th></tr></thead>
        <tbody>{_profile_rows(profile)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>ML 因子解释（峰值小时）</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>因子</th><th>名称</th><th>得分</th><th>权重</th><th>值</th></tr></thead>
        <tbody>{_factor_rows(ml)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>WRF 实测云海证据</h2>
    <p>来自本地 WRF 9km 单域 `wrfout` 后处理。云底/云顶为相对地面高度，低/中/高云为最近格点垂直层最大云量。</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>2m 温度</th><th>2m 湿度</th><th>低/中/高云</th><th>云底/云顶</th><th>低层逆温</th><th>10m 风</th></tr></thead>
        <tbody>{_wrf_rows(wrf)}</tbody>
      </table>
    </div>
    <p class="muted">证据文件：<code>{html.escape(evidence_path)}</code>；最近格点：<code>{html.escape(nearest_grid)}</code></p>
  </section>

  <section>
    <h2>WRF Readiness</h2>
    <div class="grid">
      <div class="card {missing_class}">缺失可执行文件<br><strong>{html.escape(missing_executables)}</strong></div>
      <div class="card">固定资源目录<br><strong>{html.escape(wrf["fixed_dir"])}</strong></div>
      <div class="card">GFS 缓存 cycle<br><strong>{html.escape(', '.join(wrf["gfs_cycles"]) or "-")}</strong></div>
      <div class="card ok">已生成运行配置<br><strong>{html.escape(', '.join(wrf["prepared_runs"]) or "-")}</strong></div>
    </div>
  </section>
</main>
</body>
</html>
"""


async def main() -> None:
    parser = argparse.ArgumentParser(description="Build Wunvshan Open-Meteo/ML/WRF comparison report.")
    parser.add_argument("--hours", type=int, default=120)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()

    rule = await _predict(ml_enabled=False, hours=args.hours)
    ml = await _predict(ml_enabled=True, hours=args.hours)
    date_key = (_score_summary(ml).get("date") or datetime.now().date().isoformat())
    profile = await build_meteo_profile(
        lat=WUNVSHAN["lat"],
        lng=WUNVSHAN["lng"],
        date_key=date_key,
        elevation=WUNVSHAN["elevation"],
    )
    wrf = _wrf_readiness(args.work_root)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"wunvshan_openmeteo_ml_wrf_compare_{stamp}.json"
    html_path = args.output_dir / f"wunvshan_openmeteo_ml_wrf_compare_{stamp}.html"
    json_path.write_text(
        json.dumps({"rule": rule, "ml": ml, "profile": profile, "wrf": wrf}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(_html_report(rule, ml, profile, wrf), encoding="utf-8")
    print(html_path)
    print(json_path)


if __name__ == "__main__":
    asyncio.run(main())
