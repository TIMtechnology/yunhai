#!/usr/bin/env python3
"""云海 ML 评估报告：LOOCV + 部署模型指标，输出自包含 HTML（Chart.js）。"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

ROOT = Path(__file__).resolve().parents[1]
for _base in (ROOT / "backend", ROOT):
    if (_base / "app" / "engine").exists():
        sys.path.insert(0, str(_base))
        break

from train_cloudsea_model import load_dataset, train_eval  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


def _metrics(
    y: np.ndarray,
    probs: np.ndarray,
    *,
    label: str,
    threshold: float = 0.5,
) -> dict:
    pred = (probs >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    out: dict = {
        "label": label,
        "threshold": threshold,
        "n": int(len(y)),
        "positive": int(y.sum()),
        "negative": int(len(y) - y.sum()),
        "accuracy": float(accuracy_score(y, pred)),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "specificity": float(tn / (tn + fp)) if (tn + fp) else 0.0,
        "brier": float(brier_score_loss(y, probs)),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    try:
        out["roc_auc"] = float(roc_auc_score(y, probs))
    except ValueError:
        out["roc_auc"] = None
    fpr, tpr, thresholds = roc_curve(y, probs)
    out["roc"] = {
        "fpr": [float(x) for x in fpr],
        "tpr": [float(x) for x in tpr],
        "thresholds": [float(x) for x in thresholds],
    }
    return out


def _calibration(y: np.ndarray, probs: np.ndarray, n_bins: int = 10) -> dict:
    bins = np.linspace(0, 1, n_bins + 1)
    centers: list[float] = []
    pred_mean: list[float] = []
    actual_rate: list[float] = []
    counts: list[int] = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (probs >= lo) & (probs < hi if i < n_bins - 1 else probs <= hi)
        if not mask.any():
            continue
        centers.append(float((lo + hi) / 2))
        pred_mean.append(float(probs[mask].mean()))
        actual_rate.append(float(y[mask].mean()))
        counts.append(int(mask.sum()))
    return {
        "centers": centers,
        "pred_mean": pred_mean,
        "actual_rate": actual_rate,
        "counts": counts,
    }


def _prob_histogram(probs: np.ndarray, y: np.ndarray) -> dict:
    edges = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    pos_counts = []
    neg_counts = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i] / 100, edges[i + 1] / 100
        mask = (probs >= lo) & (probs < hi if i < len(edges) - 2 else probs <= hi)
        pos_counts.append(int((mask & (y >= 0.5)).sum()))
        neg_counts.append(int((mask & (y < 0.5)).sum()))
    return {"edges": edges, "positive": pos_counts, "negative": neg_counts}


def _find_model(models_dir: Path, spot_id: str, viewpoint_id: str) -> Path | None:
    from app.engine.ml_eligibility import spot_model_path

    candidates = [
        spot_model_path(spot_id, viewpoint_id, models_dir=models_dir),
        models_dir / f"spot_{spot_id}_{viewpoint_id}.pkl",
        Path("/app/data/cloudsea/models") / f"spot_{spot_id}_{viewpoint_id}.pkl",
        ROOT / "data" / "cloudsea" / "models" / f"spot_{spot_id}_{viewpoint_id}.pkl",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def build_comparison_report(
    *,
    db_path: Path,
    spot_id: str,
    viewpoint_id: str,
    models_dir: Path,
    db_only: bool = True,
) -> dict:
    """线上 DB：baseline vs 增强 v6（LOOCV 重算，与部署 pkl 对比）。"""
    from app.engine.ml_tuning import DEFAULT_C_GRID, train_tuned

    X, y, meta, feature_names = load_dataset(
        db_path,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        exclude_rain=True,
        db_only=db_only,
    )
    if len(y) < 5 or len(set(y)) < 2:
        raise RuntimeError(f"样本不足或仅单类：n={len(y)}")

    model_b, loo_b, pred_b = train_eval(X, y, c=0.3)
    baseline = {
        "version": "baseline",
        "params": {
            "algorithm": "LogisticRegression L2",
            "C": 0.3,
            "class_weight": "balanced",
            "threshold": 0.5,
            "feature_count": len(feature_names),
            "calibration": "无",
            "feature_selection": "全特征",
        },
        "metrics": _metrics(y, loo_b, label="baseline LOOCV", threshold=0.5),
        "calibration": _calibration(y, loo_b),
        "histogram": _prob_histogram(loo_b, y),
        "in_sample_acc": float(accuracy_score(y, (model_b.predict_proba(X)[:, 1] >= 0.5).astype(int))),
    }

    tuned = train_tuned(X, y, meta, feature_names, c_grid=DEFAULT_C_GRID)
    thr = tuned.decision_threshold
    enhanced = {
        "version": "cloudsea_ml_v6_tuned",
        "params": {
            "algorithm": "LogisticRegression L2",
            "C": tuned.c,
            "C_grid": list(DEFAULT_C_GRID),
            "C_grid_f1": {str(k): round(v, 3) for k, v in tuned.c_grid_scores.items()},
            "class_weight": "balanced",
            "threshold": round(thr, 3),
            "threshold_objective": "F1 (LOOCV)",
            "feature_count_full": len(feature_names),
            "feature_count_selected": len(tuned.feature_names),
            "selected_features": tuned.feature_names,
            "calibration": "IsotonicRegression (LOOCV 概率拟合)",
            "feature_selection": tuned.feature_strategy,
            "feature_strategy": tuned.feature_strategy,
            "feature_note": "默认全特征 + C网格 + 等渗校准 + F1阈值（L1筛特征在部分环境可达更高LOOCV但不稳定）",
        },
        "metrics": _metrics(y, tuned.loo_probs_calibrated, label="enhanced LOOCV", threshold=thr),
        "metrics_at_05": _metrics(y, tuned.loo_probs_calibrated, label="enhanced @0.5", threshold=0.5),
        "calibration": _calibration(y, tuned.loo_probs_calibrated),
        "histogram": _prob_histogram(tuned.loo_probs_calibrated, y),
        "monthly_cv": tuned.monthly_cv,
    }

    model_path = _find_model(models_dir, spot_id, viewpoint_id)
    deployed: dict | None = None
    if model_path:
        with open(model_path, "rb") as f:
            art = pickle.load(f)
        deployed = {
            "path": str(model_path),
            "version": art.get("version"),
            "n_days": art.get("n_days"),
            "trained_at": art.get("trained_at"),
            "saved_loocv": art.get("loocv_accuracy"),
            "saved_brier": art.get("loocv_brier"),
            "tuning_c": art.get("tuning_c"),
            "decision_threshold": art.get("decision_threshold"),
            "selected_feature_names": art.get("selected_feature_names"),
        }

    daily = []
    for i, m in enumerate(meta):
        p_base = float(loo_b[i])
        p_enh = float(tuned.loo_probs_calibrated[i])
        pred_enh = int(p_enh >= thr)
        daily.append(
            {
                "date": m["date"],
                "status": m["status"],
                "actual": int(y[i]),
                "p_baseline": round(p_base * 100, 1),
                "p_enhanced": round(p_enh * 100, 1),
                "pred_baseline": int(pred_b[i]),
                "pred_enhanced": pred_enh,
                "ok_baseline": bool(pred_b[i] == y[i]),
                "ok_enhanced": bool(pred_enh == y[i]),
                "delta": "fix" if pred_b[i] != y[i] and pred_enh == y[i] else (
                    "regress" if pred_b[i] == y[i] and pred_enh != y[i] else ""
                ),
            }
        )

    eb = enhanced["metrics"]
    bb = baseline["metrics"]
    return {
        "report_type": "v6_comparison",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "db_path": str(db_path),
        "feature_count": len(feature_names),
        "deployed": deployed,
        "baseline": baseline,
        "enhanced": enhanced,
        "comparison": {
            "accuracy_delta_pp": round((eb["accuracy"] - bb["accuracy"]) * 100, 1),
            "f1_delta_pp": round((eb["f1"] - bb["f1"]) * 100, 1),
            "brier_delta": round(eb["brier"] - bb["brier"], 3),
            "errors_baseline": int((pred_b != y).sum()),
            "errors_enhanced": int((np.array([d["pred_enhanced"] for d in daily]) != y).sum()),
            "fixed_days": sum(1 for d in daily if d["delta"] == "fix"),
            "regress_days": sum(1 for d in daily if d["delta"] == "regress"),
        },
        "daily": daily,
    }


def build_report(
    *,
    db_path: Path,
    spot_id: str,
    viewpoint_id: str,
    models_dir: Path,
    db_only: bool = True,
) -> dict:
    X, y, meta, feature_names = load_dataset(
        db_path,
        spot_id=spot_id,
        viewpoint_id=viewpoint_id,
        exclude_rain=True,
        db_only=db_only,
    )
    if len(y) < 5 or len(set(y)) < 2:
        raise RuntimeError(f"样本不足或仅单类：n={len(y)}")

    model, loo_probs, loo_pred = train_eval(X, y)
    full_probs = model.predict_proba(X)[:, 1]

    model_path = _find_model(models_dir, spot_id, viewpoint_id)
    artifact_info: dict | None = None
    if model_path:
        with open(model_path, "rb") as f:
            art = pickle.load(f)
        artifact_info = {
            "path": str(model_path),
            "version": art.get("version"),
            "n_days": art.get("n_days"),
            "trained_at": art.get("trained_at"),
            "saved_loocv": art.get("loocv_accuracy"),
            "saved_brier": art.get("loocv_brier"),
        }

    rows = []
    for i, m in enumerate(meta):
        rows.append(
            {
                "date": m["date"],
                "status": m["status"],
                "actual": int(y[i]),
                "p_loo": round(float(loo_probs[i]) * 100, 1),
                "p_full": round(float(full_probs[i]) * 100, 1),
                "pred_loo": int(loo_pred[i]),
                "ok_loo": bool(loo_pred[i] == y[i]),
            }
        )

    return {
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "spot_id": spot_id,
        "viewpoint_id": viewpoint_id,
        "db_path": str(db_path),
        "feature_count": len(feature_names),
        "artifact": artifact_info,
        "loocv": _metrics(y, loo_probs, label="LOOCV（留一日交叉验证）"),
        "in_sample": _metrics(y, full_probs, label="全量拟合（易乐观，仅作对比）"),
        "calibration_loo": _calibration(y, loo_probs),
        "calibration_full": _calibration(y, full_probs),
        "histogram": _prob_histogram(loo_probs, y),
        "daily": rows,
        "gap_accuracy": float(accuracy_score(y, (full_probs >= 0.5).astype(int)))
        - float(accuracy_score(y, loo_pred)),
    }


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    loocv = data["loocv"]
    cm = loocv["confusion"]
    auc = loocv.get("roc_auc")
    auc_s = f"{auc * 100:.1f}%" if auc is not None else "—"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>云海 ML 评估 · {data['spot_id']}/{data['viewpoint_id']}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: #0f172a; color: #e2e8f0; }}
    body {{ margin: 0; padding: 24px; max-width: 1100px; margin-inline: auto; }}
    h1 {{ font-size: 1.35rem; color: #7dd3fc; }}
    h2 {{ font-size: 1.05rem; margin-top: 2rem; color: #94a3b8; border-bottom: 1px solid #334155; padding-bottom: 6px; }}
    .muted {{ color: #94a3b8; font-size: 0.9rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 14px; }}
    .card b {{ display: block; font-size: 1.4rem; color: #f8fafc; }}
    .card span {{ font-size: 0.75rem; color: #94a3b8; }}
    .explain {{ background: #172554; border-left: 3px solid #3b82f6; padding: 12px 14px; margin: 12px 0; font-size: 0.88rem; line-height: 1.55; }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    @media (max-width: 800px) {{ .charts {{ grid-template-columns: 1fr; }} }}
    .chart-box {{ background: #1e293b; border-radius: 10px; padding: 12px; min-height: 280px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 12px; }}
    th, td {{ border: 1px solid #334155; padding: 6px 8px; text-align: left; }}
    th {{ background: #1e293b; }}
    tr.ok {{ background: rgba(34,197,94,0.08); }}
    tr.bad {{ background: rgba(239,68,68,0.12); }}
    .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; }}
    .tag-pos {{ background: #14532d; color: #86efac; }}
    .tag-neg {{ background: #450a0a; color: #fca5a5; }}
  </style>
</head>
<body>
  <h1>云海 ML 评估报告</h1>
  <p class="muted">{data['spot_id']} · {data['viewpoint_id']} · 生成于 {data['generated_at']} · 特征 {data['feature_count']} 维 · 有效样本 {loocv['n']} 天（排除日出降水日）</p>

  <div class="explain">
    <strong>如何读这份报告</strong><br/>
    本项目是<strong>按日</strong>二分类（日出窗口 03–07 是否有观赏级云海）。样本约 {loocv['n']} 天，必须用 <strong>LOOCV（留一日验证）</strong> 估计真实泛化能力；「全量拟合」是在全部数据上训练再在同一批数据上打分，通常会<strong>偏乐观</strong>。<br/>
    <strong>准确率</strong>：判对的比例。<strong>精确率</strong>：预测「有云海」里真有云海的比例（少误报）。<strong>召回率</strong>：真实有云海的日子被找出来的比例（少漏报）。<strong>ROC-AUC</strong>：排序能力，0.5=瞎猜，1=完美。<strong>Brier</strong>：概率校准误差，越小越好。
  </div>

  <h2>核心指标（LOOCV，阈值 50%）</h2>
  <div class="grid">
    <div class="card"><b>{loocv['accuracy']*100:.1f}%</b><span>准确率 Accuracy</span></div>
    <div class="card"><b>{loocv['precision']*100:.1f}%</b><span>精确率 Precision</span></div>
    <div class="card"><b>{loocv['recall']*100:.1f}%</b><span>召回率 Recall</span></div>
    <div class="card"><b>{loocv['f1']*100:.1f}%</b><span>F1 分数</span></div>
    <div class="card"><b>{loocv['specificity']*100:.1f}%</b><span>特异度（真负例识别）</span></div>
    <div class="card"><b>{auc_s}</b><span>ROC-AUC</span></div>
    <div class="card"><b>{loocv['brier']:.3f}</b><span>Brier 分数</span></div>
    <div class="card"><b>{loocv['positive']}/{loocv['negative']}</b><span>有云海 / 无云海 样本</span></div>
  </div>

  <h2>混淆矩阵（LOOCV）</h2>
  <div class="explain">
    行=真实，列=预测。TN=真无云且判无；FP=无云却判有（误报）；FN=有云却判无（漏报）；TP=有云且判有。
  </div>
  <table>
    <tr><th></th><th>预测：无</th><th>预测：有</th></tr>
    <tr><th>真实：无</th><td>TN = {cm['tn']}</td><td>FP = {cm['fp']}</td></tr>
    <tr><th>真实：有</th><td>FN = {cm['fn']}</td><td>TP = {cm['tp']}</td></tr>
  </table>

  <p class="muted">全量拟合准确率 {data['in_sample']['accuracy']*100:.1f}%（与 LOOCV 差距 {data['gap_accuracy']*100:+.1f} 个百分点，差距大说明过拟合风险高）</p>

  <h2>图表</h2>
  <div class="charts">
    <div class="chart-box"><canvas id="rocChart"></canvas></div>
    <div class="chart-box"><canvas id="calChart"></canvas></div>
    <div class="chart-box"><canvas id="histChart"></canvas></div>
    <div class="chart-box"><canvas id="cmChart"></canvas></div>
  </div>

  <h2>逐日明细（LOOCV 概率）</h2>
  <table id="dailyTable">
    <thead><tr><th>日期</th><th>标注</th><th>真实</th><th>P(LOOCV)</th><th>P(全量)</th><th>LOOCV判</th><th>对错</th></tr></thead>
    <tbody></tbody>
  </table>

  <script>
  const DATA = {payload};
  const loocv = DATA.loocv;

  new Chart(document.getElementById('rocChart'), {{
    type: 'line',
    data: {{
      labels: loocv.roc.fpr.map((x,i) => (x*100).toFixed(0)+'% FPR'),
      datasets: [{{
        label: 'ROC (LOOCV) AUC=' + (loocv.roc_auc != null ? (loocv.roc_auc*100).toFixed(1)+'%' : '—'),
        data: loocv.roc.tpr.map((t,i) => ({{x: loocv.roc.fpr[i]*100, y: t*100}})),
        borderColor: '#38bdf8',
        backgroundColor: 'rgba(56,189,248,0.1)',
        fill: true,
        tension: 0.2,
        pointRadius: 0,
      }}, {{
        label: '随机猜测',
        data: [{{x:0,y:0}},{{x:100,y:100}}],
        borderColor: '#64748b',
        borderDash: [6,4],
        pointRadius: 0,
      }}],
    }},
    options: {{
      parsing: false,
      scales: {{
        x: {{ type: 'linear', min: 0, max: 100, title: {{ display: true, text: '假阳性率 FPR %', color: '#94a3b8' }} }},
        y: {{ min: 0, max: 100, title: {{ display: true, text: '真阳性率 TPR %', color: '#94a3b8' }} }},
      }},
      plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
    }},
  }});

  const cal = DATA.calibration_loo;
  new Chart(document.getElementById('calChart'), {{
    type: 'bar',
    data: {{
      labels: cal.centers.map(c => (c*100).toFixed(0)+'%'),
      datasets: [
        {{ label: '预测概率均值', data: cal.pred_mean.map(x => x*100), backgroundColor: 'rgba(56,189,248,0.6)' }},
        {{ label: '实际有云海比例', data: cal.actual_rate.map(x => x*100), backgroundColor: 'rgba(74,222,128,0.6)' }},
      ],
    }},
    options: {{
      scales: {{ y: {{ min: 0, max: 100, title: {{ display: true, text: '%', color: '#94a3b8' }} }} }},
      plugins: {{ title: {{ display: true, text: '校准图（LOOCV）', color: '#e2e8f0' }}, legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
    }},
  }});

  const h = DATA.histogram;
  const labels = h.edges.slice(0,-1).map((e,i) => e+'–'+h.edges[i+1]+'%');
  new Chart(document.getElementById('histChart'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        {{ label: '标注：有云海', data: h.positive, backgroundColor: 'rgba(74,222,128,0.7)' }},
        {{ label: '标注：无云海', data: h.negative, backgroundColor: 'rgba(248,113,113,0.7)' }},
      ],
    }},
    options: {{
      scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, title: {{ display: true, text: '天数', color: '#94a3b8' }} }} }},
      plugins: {{ title: {{ display: true, text: 'LOOCV 概率分布', color: '#e2e8f0' }}, legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
    }},
  }});

  new Chart(document.getElementById('cmChart'), {{
    type: 'bar',
    data: {{
      labels: ['TN','FP','FN','TP'],
      datasets: [{{ data: [loocv.confusion.tn, loocv.confusion.fp, loocv.confusion.fn, loocv.confusion.tp],
        backgroundColor: ['#334155','#f87171','#fb923c','#4ade80'] }}],
    }},
    options: {{
      plugins: {{ title: {{ display: true, text: '混淆矩阵计数', color: '#e2e8f0' }}, legend: {{ display: false }} }},
      scales: {{ y: {{ beginAtZero: true }} }},
    }},
  }});

  const tbody = document.querySelector('#dailyTable tbody');
  DATA.daily.forEach(r => {{
    const tr = document.createElement('tr');
    tr.className = r.ok_loo ? 'ok' : 'bad';
    const act = r.actual ? '<span class="tag tag-pos">有</span>' : '<span class="tag tag-neg">无</span>';
    const pred = r.pred_loo ? '<span class="tag tag-pos">有</span>' : '<span class="tag tag-neg">无</span>';
    tr.innerHTML = `<td>${{r.date}}</td><td>${{r.status}}</td><td>${{act}}</td><td>${{r.p_loo}}%</td><td>${{r.p_full}}%</td><td>${{pred}}</td><td>${{r.ok_loo ? '✓' : '✗'}}</td>`;
    tbody.appendChild(tr);
  }});
  </script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="生成云海 ML HTML 评估报告")
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument(
        "--models-dir",
        default=str(ROOT / "data" / "cloudsea" / "models"),
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--allow-fetch", action="store_true", help="允许缺失日请求 Open-Meteo（默认仅用 DB 缓存）")
    args = parser.parse_args()

    db_path = Path(args.db)
    models_dir = Path(args.models_dir)
    out = Path(args.output) if args.output else (
        ROOT / "data" / "cloudsea" / "reports" / f"ml_eval_{args.spot_id}_{args.viewpoint_id}.html"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    data = build_report(
        db_path=db_path,
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
        models_dir=models_dir,
        db_only=not args.allow_fetch,
    )
    html = render_html(data)
    out.write_text(html, encoding="utf-8")

    loocv = data["loocv"]
    print(f"报告已写入: {out}")
    print(f"样本 n={loocv['n']}  有云海={loocv['positive']}  无云海={loocv['negative']}")
    print(f"LOOCV  accuracy={loocv['accuracy']*100:.1f}%  precision={loocv['precision']*100:.1f}%  recall={loocv['recall']*100:.1f}%")
    print(f"       f1={loocv['f1']*100:.1f}%  roc_auc={loocv.get('roc_auc')}  brier={loocv['brier']:.3f}")
    cm = loocv["confusion"]
    print(f"混淆矩阵 TN={cm['tn']} FP={cm['fp']} FN={cm['fn']} TP={cm['tp']}")


def render_html_v6(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    bb = data["baseline"]["metrics"]
    eb = data["enhanced"]["metrics"]
    ep = data["enhanced"]["params"]
    cmp_ = data["comparison"]
    dep = data.get("deployed") or {}
    dep_loocv = dep.get("saved_loocv")
    dep_loocv_s = f"{dep_loocv * 100:.1f}%" if dep_loocv is not None else "—"

    feat_list = "".join(f"<li><code>{n}</code></li>" for n in ep.get("selected_features", []))
    c_grid_rows = "".join(
        f"<tr><td>{k}</td><td>{v*100:.1f}%</td></tr>"
        for k, v in sorted(ep.get("C_grid_f1", {}).items(), key=lambda x: float(x[0]))
    )
    monthly_rows = "".join(
        f"<tr><td>{m['month']}</td><td>{m['n']}</td><td>{m['accuracy']*100:.1f}%</td></tr>"
        for m in data["enhanced"].get("monthly_cv", {}).get("months", [])
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>云海 ML v6 增强评估 · {data['spot_id']}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }}
    body {{ margin: 0; padding: 24px; max-width: 1180px; margin-inline: auto; }}
    h1 {{ color: #7dd3fc; font-size: 1.4rem; }}
    h2 {{ color: #94a3b8; font-size: 1.05rem; margin-top: 2rem; border-bottom: 1px solid #334155; padding-bottom: 6px; }}
    .muted {{ color: #94a3b8; font-size: 0.88rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 12px; }}
    .card b {{ font-size: 1.25rem; color: #f8fafc; display: block; }}
    .card span {{ font-size: 0.72rem; color: #94a3b8; }}
    .card.enh b {{ color: #86efac; }}
    .explain {{ background: #172554; border-left: 3px solid #3b82f6; padding: 12px; font-size: 0.86rem; line-height: 1.55; margin: 12px 0; }}
    .params {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 14px; font-size: 0.85rem; }}
    .params dt {{ color: #7dd3fc; font-weight: 600; margin-top: 8px; }}
    .params dd {{ margin: 4px 0 0 16px; color: #cbd5e1; }}
    .compare {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    @media (max-width: 800px) {{ .compare {{ grid-template-columns: 1fr; }} }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .chart-box {{ background: #1e293b; border-radius: 10px; padding: 10px; min-height: 260px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
    th, td {{ border: 1px solid #334155; padding: 6px 8px; }}
    th {{ background: #1e293b; }}
    tr.ok {{ background: rgba(34,197,94,0.08); }}
    tr.bad {{ background: rgba(239,68,68,0.1); }}
    tr.fix {{ background: rgba(56,189,248,0.1); }}
    tr.regress {{ background: rgba(251,191,36,0.1); }}
    .tag {{ font-size: 0.7rem; padding: 2px 6px; border-radius: 4px; }}
    .up {{ color: #86efac; }}
    .down {{ color: #fca5a5; }}
  </style>
</head>
<body>
  <h1>云海 ML 增强版（v6 tuned）评估报告</h1>
  <p class="muted">
    {data['spot_id']} · {data['viewpoint_id']} · 数据源：线上 DB · 生成 {data['generated_at']} ·
    有效样本 {bb['n']} 天（有云海 {bb['positive']} / 无 {bb['negative']}）· 全特征 {data['feature_count']} 维
  </p>

  <div class="explain">
    <strong>相对上一版（baseline）升级了什么？</strong><br/>
    ① <strong>C 网格</strong> LOOCV 选 L2 强度（本次 <code>C={ep['C']}</code>）；② <strong>{ep.get('feature_selection','')}</strong>；③ <strong>等渗校准</strong>；④ <strong>阈值 {ep['threshold']}</strong> 按 F1 优化（非固定 50%）。数据：<strong>线上 DB · {bb['n']} 天</strong> 重算 LOOCV。
  </div>

  <h2>升级参数一览（v6）</h2>
  <div class="params">
    <dl>
      <dt>模型版本</dt><dd><code>{data['enhanced']['version']}</code></dd>
      <dt>正则化 C（选中）</dt><dd><code>{ep['C']}</code>（网格 {ep['C_grid']}，按各 C 的 LOOCV-F1 选优）</dd>
      <dt>分类阈值</dt><dd><code>{ep['threshold']}</code>（约 {ep['threshold']*100:.0f}% 概率，优化目标：{ep['threshold_objective']}）</dd>
      <dt>类别权重</dt><dd>{ep['class_weight']}（缓解有云海样本少）</dd>
      <dt>概率校准</dt><dd>{ep['calibration']}</dd>
      <dt>特征筛选</dt><dd>{ep['feature_selection']}</dd>
      <dt>保留特征（{ep['feature_count_selected']}）</dt><dd><ul style="margin:4px 0;padding-left:20px">{feat_list}</ul></dd>
      <dt>线上已部署 pkl</dt><dd>版本 <code>{dep.get('version') or '—'}</code> · 落库 LOOCV {dep_loocv_s} · 路径 <code style="font-size:0.75rem">{dep.get('path') or '—'}</code></dd>
    </dl>
    <table style="margin-top:12px;max-width:320px">
      <tr><th>C</th><th>LOOCV F1（阈值调优后）</th></tr>
      {c_grid_rows}
    </table>
  </div>

  <h2>指标对比（LOOCV）</h2>
  <div class="compare">
    <div>
      <h3 class="muted">Baseline（C=0.3 · 阈值 50% · 全特征）</h3>
      <div class="grid">
        <div class="card"><b>{bb['accuracy']*100:.1f}%</b><span>准确率</span></div>
        <div class="card"><b>{bb['f1']*100:.1f}%</b><span>F1</span></div>
        <div class="card"><b>{bb['precision']*100:.1f}%</b><span>精确率</span></div>
        <div class="card"><b>{bb['recall']*100:.1f}%</b><span>召回率</span></div>
        <div class="card"><b>{(bb.get('roc_auc') or 0)*100:.1f}%</b><span>AUC</span></div>
        <div class="card"><b>{bb['brier']:.3f}</b><span>Brier</span></div>
      </div>
      <p class="muted">错判 {cmp_['errors_baseline']} 天 · TN/FP/FN/TP = {bb['confusion']['tn']}/{bb['confusion']['fp']}/{bb['confusion']['fn']}/{bb['confusion']['tp']}</p>
    </div>
    <div>
      <h3 class="muted">Enhanced v6（C={ep['C']} · 阈值 {ep['threshold']}）</h3>
      <div class="grid">
        <div class="card enh"><b>{eb['accuracy']*100:.1f}%</b><span>准确率 <span class="up">({cmp_['accuracy_delta_pp']:+.1f}pp)</span></span></div>
        <div class="card enh"><b>{eb['f1']*100:.1f}%</b><span>F1 <span class="up">({cmp_['f1_delta_pp']:+.1f}pp)</span></span></div>
        <div class="card enh"><b>{eb['precision']*100:.1f}%</b><span>精确率</span></div>
        <div class="card enh"><b>{eb['recall']*100:.1f}%</b><span>召回率</span></div>
        <div class="card enh"><b>{(eb.get('roc_auc') or 0)*100:.1f}%</b><span>AUC</span></div>
        <div class="card enh"><b>{eb['brier']:.3f}</b><span>Brier <span class="up">({cmp_['brier_delta']:+.3f})</span></span></div>
      </div>
      <p class="muted">错判 {cmp_['errors_enhanced']} 天 · 修复 {cmp_['fixed_days']} 天 · 回归 {cmp_['regress_days']} 天</p>
    </div>
  </div>

  <h2>按月留一月验证（增强配置，辅助）</h2>
  <p class="muted">汇总准确率 {data['enhanced']['monthly_cv'].get('overall_accuracy', 0)*100:.1f}% — 月份样本不均时仅作参考，不作唯一部署依据。</p>
  <table><tr><th>月份</th><th>测试天数</th><th>准确率</th></tr>{monthly_rows}</table>

  <h2>图表（增强版 LOOCV 概率）</h2>
  <div class="charts">
    <div class="chart-box"><canvas id="rocChart"></canvas></div>
    <div class="chart-box"><canvas id="calChart"></canvas></div>
    <div class="chart-box"><canvas id="histChart"></canvas></div>
    <div class="chart-box"><canvas id="cmChart"></canvas></div>
  </div>

  <h2>逐日对比</h2>
  <table id="dailyTable">
    <thead><tr><th>日期</th><th>标注</th><th>真实</th><th>P baseline</th><th>P v6</th><th>base</th><th>v6</th><th>变化</th></tr></thead>
    <tbody></tbody>
  </table>

  <script>
  const DATA = {payload};
  const eb = DATA.enhanced.metrics;
  const cal = DATA.enhanced.calibration;
  const h = DATA.enhanced.histogram;

  new Chart(document.getElementById('rocChart'), {{
    type: 'line',
    data: {{
      datasets: [{{
        label: 'v6 LOOCV ROC AUC=' + ((eb.roc_auc||0)*100).toFixed(1) + '%',
        data: eb.roc.tpr.map((t,i) => ({{x: eb.roc.fpr[i]*100, y: t*100}})),
        borderColor: '#4ade80', fill: true, backgroundColor: 'rgba(74,222,128,0.15)', pointRadius: 0, tension: 0.2,
      }}, {{
        label: 'baseline',
        data: DATA.baseline.metrics.roc.tpr.map((t,i) => ({{x: DATA.baseline.metrics.roc.fpr[i]*100, y: t*100}})),
        borderColor: '#64748b', borderDash: [4,3], pointRadius: 0, tension: 0.2,
      }}],
    }},
    options: {{
      parsing: false,
      scales: {{
        x: {{ type: 'linear', min: 0, max: 100, title: {{ display: true, text: 'FPR %' }} }},
        y: {{ min: 0, max: 100, title: {{ display: true, text: 'TPR %' }} }},
      }},
      plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
    }},
  }});

  new Chart(document.getElementById('calChart'), {{
    type: 'bar',
    data: {{
      labels: cal.centers.map(c => (c*100).toFixed(0)+'%'),
      datasets: [
        {{ label: '预测均值', data: cal.pred_mean.map(x => x*100), backgroundColor: 'rgba(56,189,248,0.6)' }},
        {{ label: '实际有云海%', data: cal.actual_rate.map(x => x*100), backgroundColor: 'rgba(74,222,128,0.6)' }},
      ],
    }},
    options: {{ scales: {{ y: {{ max: 100 }} }}, plugins: {{ title: {{ display: true, text: '校准（v6）' }} }} }},
  }});

  const labels = h.edges.slice(0,-1).map((e,i) => e+'-'+h.edges[i+1]);
  new Chart(document.getElementById('histChart'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        {{ label: '有云海', data: h.positive, backgroundColor: 'rgba(74,222,128,0.7)' }},
        {{ label: '无云海', data: h.negative, backgroundColor: 'rgba(248,113,113,0.7)' }},
      ],
    }},
    options: {{ scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }}, plugins: {{ title: {{ display: true, text: 'v6 概率分布' }} }} }},
  }});

  const cm = eb.confusion;
  new Chart(document.getElementById('cmChart'), {{
    type: 'bar',
    data: {{
      labels: ['TN','FP','FN','TP'],
      datasets: [{{ data: [cm.tn, cm.fp, cm.fn, cm.tp], backgroundColor: ['#475569','#f87171','#fb923c','#4ade80'] }}],
    }},
    options: {{ plugins: {{ title: {{ display: true, text: 'v6 混淆矩阵' }}, legend: {{ display: false }} }} }},
  }});

  const tbody = document.querySelector('#dailyTable tbody');
  DATA.daily.forEach(r => {{
    const tr = document.createElement('tr');
    tr.className = r.ok_enhanced ? (r.delta === 'fix' ? 'fix' : 'ok') : (r.delta === 'regress' ? 'regress' : 'bad');
    const ch = r.delta === 'fix' ? '修复' : (r.delta === 'regress' ? '回归' : '');
    tr.innerHTML = `<td>${{r.date}}</td><td>${{r.status}}</td><td>${{r.actual ? '有' : '无'}}</td>
      <td>${{r.p_baseline}}%</td><td>${{r.p_enhanced}}%</td>
      <td>${{r.pred_baseline ? '有' : '无'}}</td><td>${{r.pred_enhanced ? '有' : '无'}}</td><td>${{ch}}</td>`;
    tbody.appendChild(tr);
  }});
  </script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="生成云海 ML HTML 评估报告")
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument(
        "--models-dir",
        default=str(ROOT / "data" / "cloudsea" / "models"),
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--allow-fetch", action="store_true", help="允许缺失日请求 Open-Meteo（默认仅用 DB 缓存）")
    parser.add_argument("--legacy", action="store_true", help="仅生成旧版单 baseline 报告（默认 v6 对比）")
    args = parser.parse_args()

    db_path = Path(args.db)
    models_dir = Path(args.models_dir)
    use_v6 = not args.legacy
    default_name = (
        f"ml_eval_{args.spot_id}_{args.viewpoint_id}_v6.html"
        if use_v6
        else f"ml_eval_{args.spot_id}_{args.viewpoint_id}.html"
    )
    out = Path(args.output) if args.output else (ROOT / "data" / "cloudsea" / "reports" / default_name)
    out.parent.mkdir(parents=True, exist_ok=True)

    if use_v6:
        data = build_comparison_report(
            db_path=db_path,
            spot_id=args.spot_id,
            viewpoint_id=args.viewpoint_id,
            models_dir=models_dir,
            db_only=not args.allow_fetch,
        )
        html = render_html_v6(data)
        out.write_text(html, encoding="utf-8")
        eb = data["enhanced"]["metrics"]
        print(f"v6 报告已写入: {out}")
        print(f"n={eb['n']}  baseline acc={data['baseline']['metrics']['accuracy']*100:.1f}%")
        print(f"  enhanced acc={eb['accuracy']*100:.1f}% F1={eb['f1']*100:.1f}% thr={data['enhanced']['params']['threshold']}")
        print(f"  Δacc={data['comparison']['accuracy_delta_pp']:+.1f}pp  修复{data['comparison']['fixed_days']}天 回归{data['comparison']['regress_days']}天")
        return

    data = build_report(
        db_path=db_path,
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
        models_dir=models_dir,
        db_only=not args.allow_fetch,
    )
    html = render_html(data)
    out.write_text(html, encoding="utf-8")

    loocv = data["loocv"]
    print(f"报告已写入: {out}")
    print(f"样本 n={loocv['n']}  有云海={loocv['positive']}  无云海={loocv['negative']}")
    print(f"LOOCV  accuracy={loocv['accuracy']*100:.1f}%  precision={loocv['precision']*100:.1f}%  recall={loocv['recall']*100:.1f}%")
    print(f"       f1={loocv['f1']*100:.1f}%  roc_auc={loocv.get('roc_auc')}  brier={loocv['brier']:.3f}")
    cm = loocv["confusion"]
    print(f"混淆矩阵 TN={cm['tn']} FP={cm['fp']} FN={cm['fn']} TP={cm['tp']}")


if __name__ == "__main__":
    main()
