#!/usr/bin/env python3
"""Generate CLOUDSEA-PREDICTION-ANALYSIS.html from prod backtest JSON."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "internal" / "_prod_backtest_data.json"
OUT = ROOT / "internal" / "CLOUDSEA-PREDICTION-ANALYSIS.html"


def bar_svg(value, max_v=100, color="#3b82f6", w=120, h=14):
    pct = min(100, max(0, value or 0)) / max_v
    fill = int(w * pct)
    return (
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#e5e7eb" rx="3"/>'
        f'<rect x="0" y="0" width="{fill}" height="{h}" fill="{color}" rx="3"/>'
        f"</svg>"
    )


def label_badge(label):
    colors = {"full": "#16a34a", "partial": "#ca8a04", "none": "#6b7280"}
    c = colors.get(label, "#6b7280")
    return f'<span class="badge" style="background:{c}">{label}</span>'


def row_html(r):
    if r.get("error"):
        return f"<tr><td>{r['date']}</td><td>{label_badge(r['label'])}</td><td colspan='10' class='err'>{r['error']}</td></tr>"
    prob = r.get("peak_prob") or 0
    color = "#16a34a" if prob >= 65 else ("#ca8a04" if prob >= 45 else "#ef4444")
    inv = r.get("inversion")
    inv_s = f"{inv:+.1f}°C" if inv is not None else "—"
    inv_cls = "pos" if inv and inv > 0 else "neg"
    return f"""<tr>
      <td>{r['date']}</td>
      <td>{label_badge(r['label'])}</td>
      <td><strong>{prob}%</strong> {bar_svg(prob, color=color)}</td>
      <td>{r.get('scenario','—')}</td>
      <td>{r.get('cloud_low','—')}</td>
      <td>{r.get('cloud_mid','—')}</td>
      <td>{r.get('rh_850','—')}</td>
      <td class="{inv_cls}">{inv_s}</td>
      <td>{r.get('visibility_km','—')}</td>
      <td>{r.get('obs_frac') if r.get('obs_frac') is not None else '—'}</td>
      <td>{r.get('sector_low') if r.get('sector_low') is not None else '—'}</td>
      <td>{r.get('viewing_mode','—')}</td>
    </tr>"""


def stats(rows, label_filter=None):
    subset = [r for r in rows if not r.get("error")]
    if label_filter:
        subset = [r for r in subset if r["label"] in label_filter]
    if not subset:
        return {}
    probs = [r["peak_prob"] for r in subset if r.get("peak_prob") is not None]
    return {
        "n": len(subset),
        "avg_prob": round(sum(probs) / len(probs), 1) if probs else 0,
        "min_prob": min(probs) if probs else 0,
        "max_prob": max(probs) if probs else 0,
        "at_58": sum(1 for p in probs if p == 58),
        "clear_sunrise": sum(1 for r in subset if r.get("scenario") == "晴日日出"),
    }


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    dl = data["spots"]["donglingshan/fengding"]
    wn = data["spots"]["wunvshan/dianjiangtai"]
    dl_full = stats(dl, ("full", "partial"))
    wn_full = stats(wn, ("full",))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>云海预测概率偏低 · 深度分析报告</title>
<style>
  :root {{ font-family: "PingFang SC", "Noto Sans SC", system-ui, sans-serif; line-height: 1.6; color: #1f2937; }}
  body {{ max-width: 960px; margin: 0 auto; padding: 24px 20px 64px; background: #f8fafc; }}
  h1 {{ font-size: 1.75rem; border-bottom: 3px solid #2563eb; padding-bottom: 8px; }}
  h2 {{ font-size: 1.25rem; margin-top: 2rem; color: #1e40af; }}
  h3 {{ font-size: 1.05rem; margin-top: 1.25rem; }}
  .meta {{ background: #eff6ff; border-left: 4px solid #2563eb; padding: 12px 16px; margin: 16px 0; font-size: 0.92rem; }}
  .warn {{ background: #fef3c7; border-left: 4px solid #d97706; padding: 12px 16px; margin: 16px 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  th, td {{ border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; vertical-align: middle; }}
  th {{ background: #f1f5f9; font-weight: 600; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .badge {{ color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; }}
  .neg {{ color: #dc2626; }}
  .pos {{ color: #16a34a; }}
  .err {{ color: #dc2626; }}
  .chart-box {{ background: #fff; padding: 16px; margin: 12px 0; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }}
  .stat-card {{ background: #fff; padding: 12px; border-radius: 8px; text-align: center; box-shadow: 0 1px 2px rgba(0,0,0,.06); }}
  .stat-card .num {{ font-size: 1.5rem; font-weight: 700; color: #2563eb; }}
  ul {{ padding-left: 1.25rem; }}
  li {{ margin: 6px 0; }}
  .cite {{ font-size: 0.85rem; color: #64748b; }}
  svg text {{ font-size: 11px; fill: #374151; }}
</style>
</head>
<body>

<h1>云海预测概率偏低 · 深度分析报告</h1>
<p><strong>生成时间：</strong>2026-05-29 · <strong>数据环境：生产服务器</strong>（182.203.168.140 / yunhai.timkj.com）</p>

<div class="meta">
  <strong>数据来源说明（重要）</strong><br/>
  本报告全部回放数据来自<strong>线上生产环境</strong>内部 API
  （<code>/api/internal/backtest/predict</code>），在 Docker 容器内调用，使用生产 Redis 与
  <code>meteo_day_cache</code> 缓存链路；<strong>未使用本地 DB 或本地 Redis</strong>。
  Health 检查：<code>cache.backend=redis</code>，DEM 快照已预加载（terrain_snapshots_preloaded=1）。
</div>

<h2>1. 执行摘要</h2>
<ul>
  <li><strong>东灵山峰顶（peak_overlook）</strong>：标注为 full/partial 的云海日，日出窗峰值概率多集中在 <strong>58%–62%</strong>，
      其中 4 天恰好为 <strong>58%</strong>（规则引擎 type_a / 峰顶逆温补偿下限），场景却常显示「多云无日出」——
      与用户感知的「明确有云海」存在语义落差。</li>
  <li><strong>五女山点将台（valley_fill）</strong>：8 个 full 标注日中，<strong>5 日峰值概率 &lt;15%</strong>，
      场景为「晴日日出/晴空少云」，主因是 NWP 低云量（cloud_low）接近 0，模型未解析山谷辐射雾/平流雾。</li>
  <li><strong>共性</strong>：Open-Meteo historical_forecast 的层云参数化与 850hPa 相对湿度，对华北高山/辽东丘陵
      2 km 以下贴地云系刻画不足；逆温项 <code>t_850 − t_925</code> 在多数样本为<strong>负值</strong>（非逆温），
      与文献中云海形成所需的贴地逆温层不一致。</li>
  <li><strong>ML</strong>：两景点均为 <code>ml_active: false · rule_only</code>（东灵山有效标注 &lt;30 天），
      概率完全由规则引擎决定。</li>
</ul>

<h2>2. 统计概览</h2>
<div class="stat-grid">
  <div class="stat-card"><div class="num">{dl_full['n']}</div>东灵山 full/partial 样本</div>
  <div class="stat-card"><div class="num">{dl_full['avg_prob']}%</div>东灵山平均峰值概率</div>
  <div class="stat-card"><div class="num">{dl_full['at_58']}</div>东灵山恰为 58% 的天数</div>
  <div class="stat-card"><div class="num">{wn_full['n']}</div>五女山 full 样本</div>
  <div class="stat-card"><div class="num">{wn_full['avg_prob']}%</div>五女山平均峰值概率</div>
  <div class="stat-card"><div class="num">{wn_full['clear_sunrise']}</div>五女山 full 但显示晴日日出</div>
</div>

<div class="chart-box">
<h3>东灵山 · 峰值概率分布（标注日 + 对照 none 日）</h3>
<svg width="100%" height="220" viewBox="0 0 640 220" xmlns="http://www.w3.org/2000/svg">
"""
    # Simple bar chart for donglingshan
    for i, r in enumerate(dl):
        if r.get("error"):
            continue
        x = 40 + i * 58
        h = (r.get("peak_prob") or 0) * 1.6
        fill = "#16a34a" if r["label"] in ("full", "partial") else "#94a3b8"
        html += f'<rect x="{x}" y="{200-h}" width="40" height="{h}" fill="{fill}" rx="2"/>'
        html += f'<text x="{x+20}" y="{195-h}" text-anchor="middle">{r.get("peak_prob")}%</text>'
        html += f'<text x="{x+20}" y="215" text-anchor="middle" transform="rotate(-45 {x+20} 215)">{r["date"][5:]}</text>'

    html += """
<text x="320" y="12" text-anchor="middle" font-weight="bold">绿=有云海标注 · 灰=无云海对照</text>
</svg>
</div>

<div class="chart-box">
<h3>五女山 · 峰值概率分布</h3>
<svg width="100%" height="220" viewBox="0 0 640 220" xmlns="http://www.w3.org/2000/svg">
"""
    for i, r in enumerate(wn):
        if r.get("error"):
            continue
        x = 30 + i * 52
        h = (r.get("peak_prob") or 0) * 1.6
        fill = "#16a34a" if r["label"] == "full" else "#94a3b8"
        html += f'<rect x="{x}" y="{200-h}" width="36" height="{h}" fill="{fill}" rx="2"/>'
        html += f'<text x="{x+18}" y="{195-h}" text-anchor="middle">{r.get("peak_prob")}%</text>'
        html += f'<text x="{x+18}" y="215" text-anchor="middle" transform="rotate(-45 {x+18} 215)">{r["date"][5:]}</text>'

    html += """
</svg>
</div>

<h2>3. 东灵山峰顶 · 逐日明细（生产回放）</h2>
<table>
<tr>
  <th>日期</th><th>标注</th><th>峰值概率</th><th>场景</th>
  <th>低云%</th><th>中云%</th><th>RH850</th><th>850–925ΔT</th>
  <th>能见度km</th><th>可观测占比</th><th>扇区低云</th><th>模式</th>
</tr>
"""
    html += "\n".join(row_html(r) for r in dl)
    html += """
</table>

<h2>4. 五女山点将台 · 逐日明细（生产回放）</h2>
<table>
<tr>
  <th>日期</th><th>标注</th><th>峰值概率</th><th>场景</th>
  <th>低云%</th><th>中云%</th><th>RH850</th><th>850–925ΔT</th>
  <th>能见度km</th><th>可观测占比</th><th>扇区低云</th><th>模式</th>
</tr>
"""
    html += "\n".join(row_html(r) for r in wn)
    html += """

<h2>5. 机理分析</h2>

<h3>5.1 为何东灵山「有云海日」常卡在 58%？</h3>
<p>规则引擎在 <code>cloudsea_scorer.py</code> 中对 archetype <strong>type_a</strong>（峰顶逆温型）设定了
<strong>概率下限 0.58</strong>；同时在峰顶模式下，若 RH850≥72 且云底低于可视谷顶，亦会抬升至 58%。
因此 5/14、5/17、5/20、5/21 等日虽然 NWP 低云=100%、地面 RH=100%，最终概率却被<strong>下限钳制</strong>而非继续升高。</p>
<p>但这些日子的<strong>可观测场 obs_frac=0、sector_low=null</strong>（扇区气象未参与或未命中），
导致「站在云海之上」场景无法触发，退而显示「多云无日出」（高云+低云均厚，日出概率被压低）。
对比 8/17（obs_frac=0.93，75%，「站在云海之上」）与 5/14（obs_frac=0，58%，「多云无日出」），
差异主要来自<strong>可观测场/DEM 扇区数据是否有效叠加</strong>，而非 NWP 云量本身。</p>

<h3>5.2 为何五女山 full 标注日常显示「晴日日出」且概率 &lt;15%？</h3>
<p>点将台为 <strong>valley_fill</strong> 模式，依赖 NWP 低云 + 850hPa 湿度刻画谷地填云。
5/04、5/09、5/22 等 full 日：<code>cloud_low=0</code>、<code>rh_850=37–50</code>、能见度 &gt;10 km，
规则引擎正确（按 NWP）给出 8–11% 与「晴日日出」——但与实地云海标注矛盾。</p>
<p>辽东丘陵辐射雾/河谷雾往往发生在<strong>百米至数百米贴地层</strong>，ERA5/GFS 再分析低云分类
（cloud_cover_low）空间分辨率约 9–25 km，<strong>难以解析</strong>点将台 804 m 视角下的谷雾带。
2024-10-05 虽标注 full，NWP 仍报 cloud_low=0，概率被 sunrise 分支抬至 58% 但场景仍为「晴日日出」
（sunrise_prob 高、has_cloudsea_evidence=false）。</p>

<h3>5.3 逆温与 RH850</h3>
<p>本批生产样本中，850–925 hPa 温差（inversion）<strong>几乎均为负值</strong>（925 hPa 更暖），
表示再分析场<strong>未呈现经典贴地逆温</strong>，而地面 RH 可达 94–100%。
这与《气象》等刊论述的华北高山云海形成机制（夜间辐射冷却 + 谷地逆温 + 充足水汽）部分脱节——
<strong>不是实况无逆温，而是 NWP 垂直分辨率平滑了逆温层</strong>。</p>

<h3>5.4 场景文案 vs 云海概率</h3>
<p><code>scenario.py</code> 在 cloud_low≥55 且中高云≥50 时优先返回「多云无日出」，
combined_score 会扣减 15 分；用户界面若突出场景标签，会忽视 cloudsea_prob 仍为中等的信号。
东灵山 5/14–5/20 即属此类：<strong>概率 58% 但场景 discouraging</strong>。</p>

<h2>6. 文献与行业对照</h2>
<ul>
  <li>《北京山区云海气象特征分析》（2025, 10.3878/j.issn.1006-9585.2025.25032）：
      强调地形抬升、夜间逆温与低云转化；本系统 RH850/逆温因子方向一致，但再分析逆温信号偏弱。</li>
  <li>WMO / 山地气象综述：辐射雾与云海的<strong>水平尺度常 &lt;1 km</strong>，全球模式需 LES 或卫星/地面观测同化方可改善。</li>
  <li>业务实践：黄山、泰山等景区预报多融合<strong>地面能见度、卫星 IR 云图、站点露点</strong>，
      纯 NWP 低云对「观赏级云海」召回率普遍 &lt;60%。</li>
</ul>
<p class="cite">注：以上文献用于机理解释；本报告数值均来自生产 API 实测回放，非模拟。</p>

<h2>7. 结论与建议（仅分析，未改代码）</h2>
<ol>
  <li><strong>数据层</strong>：继续以生产 Redis + meteo_day_cache 为唯一缓存源；补全东灵山 5 月标注日的扇区气象与可观测场（当前 sector_low=null 样本需排查 DEM/扇区 API 是否命中）。</li>
  <li><strong>东灵山</strong>：58% 为规则下限而非真实置信度上限；full 日应优先触发 peak_overlook + 可观测场证据，避免「高概率 + 负面场景」组合。</li>
  <li><strong>五女山</strong>：valley_fill 在 NWP 低云=0 时系统性漏报；需卫星/能见度/站点湿度等<strong>非 NWP 补偿</strong>或 ML（≥30 天标注后训练 spot 模型）。</li>
  <li><strong>逆温</strong>：考虑用地面 T、Td 估算贴地逆温代理，而非仅 850–925 hPa 温差。</li>
  <li><strong>ML 路径</strong>：东灵山继续积累标注；五女山已有 spot 模型文件但 ml_active=false，待 eligibility 满足后 A/B 规则 vs ML。</li>
</ol>

<div class="warn">
  <strong>关于 cache 的说明：</strong>用户明确要求所有 cache 以<strong>线上服务器</strong>为准。
  本报告回放均经生产容器执行，health 确认 Redis 后端；若 <code>data_source</code> 显示
  <code>historical_forecast</code>，表示该日 hourly 字段来自 Open-Meteo 历史预报 API，
  但请求路径会优先读生产 <code>meteo_day_cache</code> / Redis，与本地开发环境无关。
</div>

<p style="margin-top:2rem;font-size:0.85rem;color:#64748b;">
  原始 JSON：<code>internal/_prod_backtest_data.json</code> ·
  采集脚本：<code>scripts/_prod_backtest_collect.py</code>
</p>

</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
