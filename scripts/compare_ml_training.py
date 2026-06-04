#!/usr/bin/env python3
"""对比 baseline vs 增强调参（C 网格 / L1 特征 / 校准 / 阈值 / 按月 CV）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.engine.ml_tuning import (  # noqa: E402
    DEFAULT_C_GRID,
    TunedModelResult,
    loo_predict_proba,
    metrics_at_threshold,
    train_tuned,
    tune_threshold,
)
from train_cloudsea_model import load_dataset, train_eval  # noqa: E402


def run_baseline(X: np.ndarray, y: np.ndarray) -> dict:
    _, loo_probs, loo_pred = train_eval(X, y, c=0.3)
    m05 = metrics_at_threshold(y, loo_probs, 0.5)
    return {
        "name": "baseline (C=0.3, thr=0.5)",
        "loo_probs": loo_probs,
        "metrics": m05,
        "n_errors": int((loo_pred != y).sum()),
    }


def run_enhanced(
    X: np.ndarray,
    y: np.ndarray,
    meta: list[dict],
    feature_names: list[str],
    *,
    min_recall: float | None,
) -> dict:
    tuned = train_tuned(
        X,
        y,
        meta,
        feature_names,
        c_grid=DEFAULT_C_GRID,
        use_l1_select=True,
        calibrate=True,
        threshold_objective="f1",
        min_recall=min_recall,
    )
    loo_pred = (tuned.loo_probs_calibrated >= tuned.decision_threshold).astype(int)
    return {
        "name": "enhanced (C grid + L1 + isotonic + tuned thr)",
        "tuned": tuned,
        "loo_probs": tuned.loo_probs_calibrated,
        "metrics_05": tuned.metrics_default_05,
        "metrics": tuned.metrics_tuned,
        "n_errors": int((loo_pred != y).sum()),
    }


def print_row(label: str, m: dict) -> None:
    auc = m.get("roc_auc", float("nan"))
    auc_s = f"{auc * 100:.1f}%" if auc == auc else "—"
    print(
        f"  {label:<42} acc={m['accuracy']*100:5.1f}%  "
        f"P={m['precision']*100:5.1f}%  R={m['recall']*100:5.1f}%  "
        f"F1={m['f1']*100:5.1f}%  AUC={auc_s}  Brier={m['brier']:.3f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    parser.add_argument("--spot-id", default="wunvshan")
    parser.add_argument("--viewpoint-id", default="dianjiangtai")
    parser.add_argument("--db-only", action="store_true", default=True)
    parser.add_argument("--allow-fetch", action="store_true")
    parser.add_argument("--min-recall", type=float, default=None, help="阈值调优时召回率下限")
    args = parser.parse_args()

    X, y, meta, feature_names = load_dataset(
        Path(args.db),
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
        exclude_rain=True,
        db_only=not args.allow_fetch,
    )
    print(f"数据集: {args.spot_id}/{args.viewpoint_id}  n={len(y)}  特征={len(feature_names)}  正样本={int(y.sum())}")
    if len(y) < 10:
        print("样本过少")
        sys.exit(1)

    base = run_baseline(X, y)
    enh = run_enhanced(X, y, meta, feature_names, min_recall=args.min_recall)
    tuned: TunedModelResult = enh["tuned"]

    print("\n=== 指标对比（LOOCV）===")
    print_row(base["name"], base["metrics"])
    print_row("enhanced @ 阈值 0.5（校准后）", enh["metrics_05"])
    print_row(
        f"enhanced @ 阈值 {tuned.decision_threshold:.2f}",
        enh["metrics"],
    )

    delta_acc = enh["metrics"]["accuracy"] - base["metrics"]["accuracy"]
    delta_f1 = enh["metrics"]["f1"] - base["metrics"]["f1"]
    print(f"\n相对 baseline：准确率 {delta_acc*100:+.1f} pp，F1 {delta_f1*100:+.1f} pp")
    print(f"错误天数：baseline {base['n_errors']} → enhanced {enh['n_errors']}")

    print(f"\n增强训练配置:")
    print(f"  最优 C: {tuned.c}  （网格得分 F1: {tuned.c_grid_scores})")
    print(f"  L1 保留特征 {len(tuned.feature_names)}/{len(feature_names)}:")
    for n in tuned.feature_names:
        print(f"    - {n}")
    print(f"  决策阈值: {tuned.decision_threshold:.2f}")
    print(f"  概率校准: {'isotonic' if tuned.calibrator else '无'}")

    print("\n=== 按月留一月验证（增强配置）===")
    mc = tuned.monthly_cv
    print(f"  汇总准确率: {mc['overall_accuracy']*100:.1f}%  （{mc['n_months']} 个月）")
    for row in mc.get("months", []):
        print(f"    {row['month']}: n={row['n']} acc={row['accuracy']*100:.1f}%")

    print("\n=== 仅 enhanced 判错日期 ===")
    base_pred = (base["loo_probs"] >= 0.5).astype(int)
    enh_pred = (enh["loo_probs"] >= tuned.decision_threshold).astype(int)
    for i, m in enumerate(meta):
        if base_pred[i] == y[i] and enh_pred[i] != y[i]:
            print(f"  回归 {m['date']} {m['status']} P={enh['loo_probs'][i]*100:.0f}%")
        if base_pred[i] != y[i] and enh_pred[i] == y[i]:
            print(f"  修复 {m['date']} {m['status']} P={enh['loo_probs'][i]*100:.0f}%")

    if enh["metrics"]["accuracy"] > base["metrics"]["accuracy"]:
        print("\n结论: 增强版 LOOCV 准确率更高，可考虑 --enhanced 训练并部署。")
    elif enh["metrics"]["f1"] > base["metrics"]["f1"]:
        print("\n结论: 准确率未升但 F1 更高，若更关注漏报/误报平衡可部署增强版。")
    else:
        print("\n结论: 本数据集上增强版未超过 baseline，建议继续攒标注或只做阈值/校准单项试验。")


if __name__ == "__main__":
    main()
