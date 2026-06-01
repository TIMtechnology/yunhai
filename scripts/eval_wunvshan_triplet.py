#!/usr/bin/env python3
"""五女山 5/29·5/25·5/28 三元组对比 + v4/v5 特征 LOOCV 评估（不部署）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import LeaveOneOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.engine.cloudsea_features import TRIPLET_DISCRIM_FEATURE_NAMES  # noqa: E402
from train_cloudsea_model import load_dataset, train_eval  # noqa: E402

TRIPLET = ("2026-05-29", "2026-05-25", "2026-05-28")
TRIPLET_LABELS = {"2026-05-29": "full", "2026-05-25": "none", "2026-05-28": "none"}


def _pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    C=0.3, class_weight="balanced", max_iter=3000, random_state=42
                ),
            ),
        ]
    )


def loocv_probs(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    probs = np.zeros(len(y))
    for tr, te in LeaveOneOut().split(X):
        m = _pipeline()
        m.fit(X[tr], y[tr])
        probs[te[0]] = m.predict_proba(X[te])[:, 1][0]
    return probs


def loocv_prob_for_date(X: np.ndarray, y: np.ndarray, meta: list[dict], date: str) -> float:
    idx = next(i for i, m in enumerate(meta) if m["date"] == date)
    for tr, te in LeaveOneOut().split(X):
        if te[0] != idx:
            continue
        m = _pipeline()
        m.fit(X[tr], y[tr])
        return float(m.predict_proba(X[te])[:, 1][0])
    return 0.0


def g(X, fn, i, k):
    return X[i][fn.index(k)] if k in fn else float("nan")


def eval_version(
    db: Path,
    *,
    use_mist: bool,
    label: str,
) -> dict:
    X, y, meta, fn = load_dataset(
        db,
        spot_id="wunvshan",
        viewpoint_id="dianjiangtai",
        db_only=True,
        use_mist_discrim_features=use_mist,
    )
    probs = loocv_probs(X, y)
    pred = (probs >= 0.5).astype(int)
    model, _, _ = train_eval(X, y)

    triplet: dict[str, dict] = {}
    for d in TRIPLET:
        i = next(j for j, m in enumerate(meta) if m["date"] == d)
        triplet[d] = {
            "status": meta[i]["status"],
            "loocv_p": float(probs[i]),
            "full_p": float(model.predict_proba(X[i : i + 1])[:, 1][0]),
        }

    return {
        "label": label,
        "n": len(y),
        "loocv_acc": float(accuracy_score(y, pred)),
        "triplet": triplet,
        "X": X,
        "fn": fn,
        "meta": meta,
    }


def print_feature_table(v4: dict, v5: dict) -> None:
    fn = v5["fn"]
    meta = v5["meta"]
    keys = [k for k in TRIPLET_DISCRIM_FEATURE_NAMES if k in fn]

    print("\n=== 三元组特征对比（v5 特征）===")
    header = f"{'特征':<28}" + "".join(f"{d[5:]:>10}" for d in TRIPLET)
    print(header)
    for k in keys:
        row = f"{k:<28}"
        for d in TRIPLET:
            i = next(j for j, m in enumerate(meta) if m["date"] == d)
            row += f"{g(v5['X'], fn, i, k):10.2f}"
        print(row)

    print("\n判别力（529 与 525/528 均值差）:")
    i29 = next(j for j, m in enumerate(meta) if m["date"] == TRIPLET[0])
    others = [
        next(j for j, m in enumerate(meta) if m["date"] == d) for d in TRIPLET[1:]
    ]
    scored = []
    for k in keys:
        v29 = g(v5["X"], fn, i29, k)
        v_other = float(np.mean([g(v5["X"], fn, j, k) for j in others]))
        scored.append((k, v29 - v_other, v29, v_other))
    scored.sort(key=lambda t: abs(t[1]), reverse=True)
    for k, diff, v29, vo in scored[:8]:
        print(f"  {k:<28} 529={v29:7.2f}  525/528均值={vo:7.2f}  Δ={diff:+.2f}")


def print_prob_table(v4: dict, v5: dict) -> None:
    print("\n=== 三元组 LOOCV / 全量拟合概率 ===")
    print(f"{'日期':<12} {'标注':<6} {'v4 LOOCV':>10} {'v5 LOOCV':>10} {'v5 全量':>10} {'v5Δv4':>8}")
    for d in TRIPLET:
        t4 = v4["triplet"][d]
        t5 = v5["triplet"][d]
        delta = t5["loocv_p"] - t4["loocv_p"]
        print(
            f"{d:<12} {t4['status']:<6} "
            f"{t4['loocv_p']*100:9.1f}% {t5['loocv_p']*100:9.1f}% "
            f"{t5['full_p']*100:9.1f}% {delta*100:+7.1f}pp"
        )

    print(f"\n整体 LOOCV accuracy: v4={v4['loocv_acc']*100:.1f}%  v5={v5['loocv_acc']*100:.1f}%")

    # 三元组排序：529 应高于 525/528
    p29 = v5["triplet"][TRIPLET[0]]["loocv_p"]
    p25 = v5["triplet"][TRIPLET[1]]["loocv_p"]
    p28 = v5["triplet"][TRIPLET[2]]["loocv_p"]
    ok = p29 > p25 and p29 > p28
    print(
        f"\n三元组排序 (v5 LOOCV): 529={p29*100:.1f}% > 525={p25*100:.1f}% & 528={p28*100:.1f}% "
        f"→ {'✓ 正确分离' if ok else '✗ 仍未分离'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "cloudsea" / "cloudsea.db"))
    args = parser.parse_args()
    db = Path(args.db)

    print("五女山 ML 三元组评估（不写入模型）")
    print(f"DB: {db}")

    v4 = eval_version(db, use_mist=False, label="v4")
    v5 = eval_version(db, use_mist=True, label="v5 dry_low_vis + fog_boost")

    print_feature_table(v4, v5)
    print_prob_table(v4, v5)


if __name__ == "__main__":
    main()
