# 云海 ML 增强调参方案（v6 tuned）

## 已实现能力

| 模块 | 说明 |
|------|------|
| `backend/app/engine/ml_tuning.py` | C 网格 LOOCV、L1 特征筛选、等渗校准、F1/Recall 约束阈值、按月留一月 CV |
| `scripts/compare_ml_training.py` | baseline vs enhanced 对比报告 |
| `scripts/train_cloudsea_model.py --enhanced` | 训练并写入 `cloudsea_ml_v6_tuned` pkl |
| `cloudsea_ml.predict_day_cloudsea` | 读取 `selected_feature_names` / `calibrator` / 校准概率 |
| `meteo_backfill.ensure_label_meteo_cached` | 标注保存后异步回填，失败重试 1 次 |

## 流水线（增强训练）

1. **数据**：`load_dataset(db_only=True)`，排除日出降水日。
2. **C 网格**：`{0.05, 0.1, 0.3, 1.0}`，以 LOOCV + F1 最优阈值选 C。
3. **L1 特征**：在最优 C 上全量拟合 L1，保留非零系数（至少 8 维）。
4. **LOOCV 概率**：用筛选后特征再跑 LOOCV。
5. **等渗校准**：`IsotonicRegression` 拟合 LOOCV 概率 → 标签。
6. **阈值**：在 `[0.15, 0.85]` 扫步长 0.01，最大化 F1（可选 `--min-recall`）。
7. **生产模型**：全量数据 + 筛选特征 + 最优 C 训练；artifact 存 calibrator、threshold、selected_feature_names。
8. **按月 CV**：辅助指标，月份样本极不均衡时不宜单独作为部署依据。

## 五女山 prod 快照对比（n=52，2026-06）

| 方案 | Accuracy | F1 | 错判天数 |
|------|----------|-----|----------|
| baseline C=0.3, thr=0.5 | 76.9% | 62.5% | 12 |
| enhanced thr=0.22 | **82.7%** | **69.0%** | **9** |

增强版修复例：2025-10-08、2026-05-05、2026-05-09、2026-05-12；回归例：2024-10-13。

**注意**：按月留一月在 2026-05（31 天）仅 ~16%，说明同月型态分布与跨月差异大；部署前建议继续补标注 + 看逐日表。

## 本地命令

```bash
# 对比
PYTHONPATH=backend python3 scripts/compare_ml_training.py \
  --db data/cloudsea/cloudsea_prod_snapshot.db

# 训练 v6（不覆盖生产前先 --no-save 或另存路径）
PYTHONPATH=backend python3 scripts/train_cloudsea_model.py \
  --db data/cloudsea/cloudsea.db --spot-id wunvshan --viewpoint-id dianjiangtai \
  --db-only --enhanced
```

## 部署

1. 训练产出 `spot_wunvshan_dianjiangtai.pkl`（version=`cloudsea_ml_v6_tuned`）。
2. 拷贝至 `/app/data/cloudsea/models/` 或镜像 `/app/models/`。
3. `CLOUDSEA_MODEL_PATH` 指向同目录下默认 pkl；重启容器。
4. 重跑 `scripts/ml_eval_report.py` 看校准图是否改善。
