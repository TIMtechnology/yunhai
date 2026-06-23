# 2026-06 升级说明 · ML v7 + 预测反馈闭环

> 发布日期：2026-06 · 线上已热补丁部署（未修改 `.env` / `docker-compose.prod.yml`）

---

## 1. 升级摘要

本次升级包含两条主线：

| 主线 | 内容 |
|------|------|
| **ML v7** | 五女山、东灵山点位模型升级为 v7 特征窗（precursor 12h + dawn 全量），线上 03–07 点 ML 推理改用 precursor 预报曲线 |
| **预测反馈** | 用户每次查看预测时异步落库访问快照；标注日结束后可回测 forecast vs 实况；标注页 Admin 可查看历史预测 |

---

## 2. LOOCV 提升情况

训练协议：`--window v7 --mode operational --enhanced --db-only`  
数据源：`data/cloudsea/cloudsea.prod.db`（生产同步标注库）  
排除规则：日出窗口（03–07）内有降水日不计入训练样本  

### 2.1 五女山 · 点将台（wunvshan / dianjiangtai）

| 版本 | 特征维数 | 气象窗 | 有效样本 | LOOCV 准确率 | LOOCV AUC | 备注 |
|------|----------|--------|----------|--------------|-----------|------|
| v6 tuned | 43 | 日出 03–07 | 65 | **81.5%** | — | 上一线上版本 |
| v6 enhanced（文档口径） | 43 | 日出 03–07 | — | **≈82%** | — | 见用户文档历史记录 |
| v7 hybrid | **62** | D-1 20:00 → D 07:00 + dawn 全量 | **55** | **85.5%** | **0.884** | **当前线上** |

**相对 v6 提升：+4.0 pp（81.5% → 85.5%）**

v7 样本数少于 v6（55 vs 65）的原因：operational 模式下 v7 训练对降水排除更严格，且需 precursor 窗 archive 完整；换取的是更接近真实「提前一晚看预报」的推理场景。

### 2.2 东灵山 · 峰顶（donglingshan / fengding）

| 版本 | 有效样本 | LOOCV 准确率 | 备注 |
|------|----------|--------------|------|
| 旧 spot 模型（v6 时代） | — | — | 样本较少，未单独调优 |
| **v7 hybrid** | **36** | **75.0%** | **首次 v7 专项训练并上线** |

东灵山标注量仍低于五女山，LOOCV 波动较大；后续补标注可继续提升。

### 2.3 v7 特征构成

```
V7 = v6 dawn 43 维（DAY_FEATURE_NAMES）
   + evening / night / 跨段趋势 19 维（V7_INCREMENTAL_NAMES）
   = 62 维（V7_FEATURE_NAMES）
```

关键增量特征示例：

- `evening_rh_mean` / `night_rh_mean` — 前夜湿度结构
- `delta_rh_night_to_dawn` — 夜间→日出 RH 变化（虚高 case 敏感）
- `delta_cloud_low_evening_to_dawn` — 低云演变
- `rh_monotonic_night` — 夜间湿度单调性

---

## 3. 代码与模块变更

### 3.1 新增

| 路径 | 说明 |
|------|------|
| `backend/app/services/prediction_feedback.py` | 访问快照写入、回测诊断、历史查询 |
| `scripts/reconcile_prediction_outcomes.py` | 批量回测 CLI |
| `scripts/export_prediction_feedback.py` | CSV/JSON 导出 CLI |

### 3.2 数据表（`cloudsea.db` 自动 migration）

- `prediction_access_log` — 访问瞬间：点位、预测摘要、meteo snapshot、lead_hours
- `prediction_access_outcome` — 次日回测：标注、实况气象、forecast 残差、diagnosis tags

### 3.3 修改

| 模块 | 变更 |
|------|------|
| `predictor.py` | v7 ML 使用 `_precursor_window_rows`；`run_prediction` 异步写 access log |
| `cloudsea_store.py` | 新表 CRUD + export |
| `routers/cloudsea.py` | `prediction-history` / `reconcile` / `export/feedback` API |
| `routers/api.py` | predict 传递 page_source / client_id |
| `CloudseaLabelTool.vue` | Admin「历史预测访问」面板 |
| `scripts/hot-patch-prod.sh` | 打包 v7 模型 + 全量 backend 补丁 |

### 3.4 线上模型文件

```
data/cloudsea/models/spot_wunvshan_dianjiangtai.pkl   window=v7  loocv=0.855  n=55
data/cloudsea/models/spot_donglingshan_fengding.pkl   window=v7  loocv=0.750  n=36
```

---

## 4. 新增 API

| 方法 | 路径 | 说明 |
|------|------|------|
| （内嵌） | `POST/GET /api/predict` | 自动写 `prediction_access_log` |
| GET | `/api/internal/cloudsea/prediction-history` | 标注页：某日访问列表 + outcome |
| GET | `/api/internal/cloudsea/prediction-history/{id}` | 单次访问详情 |
| POST | `/api/internal/cloudsea/reconcile?date=` | 手动触发某日回测 |
| GET | `/api/internal/cloudsea/export/feedback` | 导出 JSON/CSV |

---

## 5. 运维说明

### 训练 v7（本地）

```bash
python3 scripts/train_cloudsea_model.py \
  --db data/cloudsea/cloudsea.prod.db \
  --spot-id wunvshan --viewpoint-id dianjiangtai \
  --window v7 --mode operational --enhanced --db-only --loocv-detail

python3 scripts/train_cloudsea_model.py \
  --db data/cloudsea/cloudsea.prod.db \
  --spot-id donglingshan --viewpoint-id fengding \
  --window v7 --mode operational --enhanced --db-only
```

### 热补丁部署（不动 env）

```bash
SKIP_TRAIN=1 bash scripts/hot-patch-prod.sh
```

### 回测历史访问

```bash
python3 scripts/reconcile_prediction_outcomes.py --days-back 30
```

---

## 6. 已知限制

1. **访问 log 从本次部署起积累**，上线前无历史用户访问数据。
2. **6/23 类虚高 case** 仍可能出现（预报 dawn 偏湿）；预测反馈系统用于后续 cap / 重训验证。
3. **东灵山 LOOCV 75%**：样本 36 日，建议继续补标注。
4. 首次热补丁遗漏 `meteo_backfill.py` 导致容器 ImportError，已补打；`hot-patch-prod.sh` 已纳入该文件。

---

## 7. 相关文档

- 预测反馈方案：`internal/PREDICTION-FEEDBACK-PLAN.md`
- ML V2 训练方案：`internal/ML-TRAINING-V2-PLAN.md`
- 用户向模型说明：`docs/docs/prediction-model.html`
- 发布说明（用户版）：`docs/docs/release-notes.html`

---

*维护者：timkj · 2026-06*
