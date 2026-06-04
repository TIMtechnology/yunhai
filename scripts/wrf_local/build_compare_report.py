#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "cloudsea" / "reports" / "wrf-openmeteo-compare.html"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{unit}"
    return f"{html.escape(str(value))}{unit}"


def _row(record: dict[str, Any]) -> str:
    layers = record.get("cloud_layers") or {}
    boundary = record.get("cloud_boundary") or {}
    return (
        "<tr>"
        f"<td>{html.escape(str(record.get('time', '-')))}</td>"
        f"<td>{_fmt(record.get('temperature_2m_c'), '°C')}</td>"
        f"<td>{_fmt(record.get('relative_humidity_2m'), '%')}</td>"
        f"<td>{_fmt(record.get('wind_speed_10m'), 'm/s')}</td>"
        f"<td>{_fmt(record.get('wind_direction_10m'), '°', 0)}</td>"
        f"<td>{_fmt(layers.get('low'), '%')}</td>"
        f"<td>{_fmt(layers.get('mid'), '%')}</td>"
        f"<td>{_fmt(layers.get('high'), '%')}</td>"
        f"<td>{_fmt(boundary.get('base_m_agl'), 'm', 0)} / {_fmt(boundary.get('top_m_agl'), 'm', 0)}</td>"
        f"<td>{_fmt(record.get('low_level_inversion_c'), '°C')}</td>"
        "</tr>"
    )


def _html_document(wrf: dict[str, Any], openmeteo: dict[str, Any] | None) -> str:
    hourly = wrf.get("hourly") or []
    case = html.escape(str(wrf.get("case", "wrf")))
    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    openmeteo_summary = ""
    if openmeteo:
        peak = openmeteo.get("summary", {}).get("best_cloudsea_prob") or openmeteo.get("best_cloudsea_prob")
        openmeteo_summary = f"<p>Open-Meteo / 系统预测峰值：{_fmt(peak, '%')}</p>"
    rows = "\n".join(_row(item) for item in hourly)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WRF 区域增强对比报告 - {case}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f7fb; color: #172033; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 48px; }}
    section {{ background: #fff; border: 1px solid #e5eaf3; border-radius: 18px; padding: 20px; margin-top: 18px; box-shadow: 0 16px 40px rgba(34, 54, 84, .08); }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; }}
    p {{ line-height: 1.7; color: #42526b; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid #edf1f7; text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #f8fafc; color: #5a6b85; position: sticky; top: 0; }}
    .table-wrap {{ overflow-x: auto; }}
    .muted {{ color: #718096; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #edf1f7; border-radius: 14px; padding: 14px; background: #fbfdff; }}
  </style>
</head>
<body>
<main>
  <h1>WRF 区域增强对比报告</h1>
  <p>案例：{case}。本报告用于第一阶段本地验证，重点查看 WRF 是否提供更细的云层垂直结构、风场和逆温证据。</p>
  <p class="muted">生成时间：{generated_at}</p>

  <section>
    <h2>运行摘要</h2>
    <div class="grid">
      <div class="card">WRF 文件<br><strong>{html.escape(str(wrf.get("wrfout", "-")))}</strong></div>
      <div class="card">最近格点<br><strong>{html.escape(str(wrf.get("nearest_grid", {})))}</strong></div>
      <div class="card">小时数<br><strong>{len(hourly)}</strong></div>
    </div>
    {openmeteo_summary}
  </section>

  <section>
    <h2>WRF 云海证据时序</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>时间</th><th>温度</th><th>湿度</th><th>风速</th><th>风向</th>
            <th>低云</th><th>中云</th><th>高云</th><th>云底/云顶</th><th>低层逆温</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </section>
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a local HTML report for Open-Meteo vs WRF evidence.")
    parser.add_argument("--wrf-evidence", type=Path, required=True)
    parser.add_argument("--openmeteo-json", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    wrf = _load_json(args.wrf_evidence)
    openmeteo = _load_json(args.openmeteo_json) if args.openmeteo_json else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_html_document(wrf, openmeteo), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
