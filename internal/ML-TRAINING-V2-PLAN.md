# 云海 ML 训练升级方案 v2：前夜–清晨过程 + 预报偏差校正

> 目标：在保留逐小时开源预报的前提下，利用大量标注数据，让 ML 参与「天气过程走向」判断，减少 train/serve 偏差，区分「前夜还行、凌晨已转晴」与「前夜–凌晨持续积湿」两类日。

---

## 1. 现状与核心问题

### 1.1 当前管线（v6 tuned）

| 环节 | 做法 |
|------|------|
| 标签 | 标注日 `full/partial` → 1，`none` → 0 |
| 训练气象 | `meteo_hourly` 或 historical-forecast，**仅 03:00–07:00** |
| 特征 | `aggregate_day_features()` 聚合为日向量（均值/最大/类型计数 + `month`） |
| 推理 | 对目标日 live forecast 的 **同日 03–07** 聚合 → ML → 与规则 60–65% 融合 |
| 调参 | LOOCV + L1 特征 + 等渗校准（见 `internal/ml-tuning-plan.md`） |

### 1.2 三类结构性偏差

1. **时间窗过窄**  
   标签回答的是「日出时有没有云海」，特征却只描述「当天 3–7 点快照」。  
   有云海日：前夜–凌晨常有一段完整积湿/逆温建立；无云海日：可能 22 点仍偏湿，但 **04 点后已转干/吹散**。四小时均值会把两类混为一谈。

2. **Train / Serve 不一致**  
   - 训练：标注日的事后逐时（或该日 full-day historical API）  
   - 线上：提前 1–2 天的 **live forecast**  
   模型学到的是「早上确实那样 → 有云海」，推理却是「模型**认为**早上会那样 → 有云海」→ 预报偏乐观时系统性虚高（如 6/23）。

3. **`month` 过弱**  
   单标量无法表达「同月相似前夜过程」；标注样本按日稀疏，需要 **过程相似度** 而非仅月份。

---

## 2. 设计原则

1. **与产品一致**：用户看的是「未来第 N 天日出窗口」；训练必须模拟 **固定提前量**（默认 D-1 18:00 北京时间已发布的预报）。
2. **过程优于快照**：特征以 **前夜–清晨曲线** 为主，而非 4 小时均值。
3. **双任务分工**  
   - **任务 A（过程/偏差）**：开源预报在关键变量上是否「像有云海日的前夜演变」？需不需要 downward 校正？  
   - **任务 B（观云结果）**：在（原始或校正后）过程特征上，预测 `full/partial/none`。
4. **可解释、可回测**：每个特征和模块能在标注日上复现；6/22 vs 6/23 类 case 可逐日对照。
5. **渐进落地**：不推翻规则引擎；ML 先校正再融合，或并行输出「过程置信度」供 cap。

---

## 3. 目标架构（v2）

```
                    ┌─────────────────────────────────────┐
  Open-Meteo        │  固定提前量预报 (D-1 18:00 issue)    │
  hourly 5-day      │  窗口: D-1 20:00 → D 07:00           │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │  Layer 1: 过程特征工程               │
                    │  · 分段统计 ( evening / night / dawn )│
                    │  · 趋势 ΔRH, Δcloud_low, Δwind       │
                    │  · 逆温建立、降水48h、archetype 计数  │
                    └──────────────┬──────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          ▼                        ▼                        ▼
   ┌──────────────┐      ┌─────────────────┐      ┌─────────────────┐
   │ Layer 2a     │      │ Layer 2b        │      │ Layer 2c        │
   │ 历史类比 k-NN │      │ 轨迹偏差校正     │      │ 云海分类器       │
   │ (同点位同月)  │      │ (可选，Phase 2)  │      │ (LR + 校准)     │
   └──────┬───────┘      └────────┬────────┘      └────────┬────────┘
          │                        │                        │
          └────────────────────────┼────────────────────────┘
                                   ▼
                    ┌──────────────────────────────────────┐
                    │  Layer 3: 融合与约束                  │
                    │  · 规则分 + ML 分 + 类比投票           │
                    │  · plausibility cap（低云量/过程不一致）│
                    └──────────────────────────────────────┘
```

---

## 4. 时间窗与特征定义

### 4.1 标准观测窗（与标注对齐）

以 **标注日 D**（日出日）为基准，上海时区：

| 分段 | 本地时间 | 物理含义 |
|------|----------|----------|
| `evening` | D-1 20:00–23:00 | 日落后冷却起始、谷地开始存湿 |
| `night` | D 00:00–02:59 | 辐射冷却峰值、逆温建立 |
| `dawn` | D 03:00–06:59 | 现有日出窗口，与规则/ML 融合一致 |
| `precursor` | evening + night + dawn | 完整前夜–清晨（**主特征窗**） |

保留 `dawn` 与现网兼容；**新增** evening/night 及跨段 **趋势特征**。

### 4.2 过程特征（示例，Phase 1 新增 ~25 维）

**分段聚合（每段各 5–8 维）**  
`rh_mean/max`, `cloud_low_mean/max`, `cloud_mid_mean`, `wind_mean`, `vis_min`, `inversion_mean`

**趋势 / 形态（关键判别）**  
- `delta_rh_night_to_dawn` = dawn.rh_mean − night.rh_mean  
- `delta_cloud_low_evening_to_dawn`  
- `delta_wind_night_to_dawn`（夜间减弱常为有利；凌晨突增常为不利）  
- `rh_monotonic_night`（22→01→04 是否持续上升）  
- `cloud_low_peak_hour`（低云峰值出现在 02 还是 05）  
- `dawn_vs_night_archetype_flip`（夜间 Type B 计数 − dawn Type B 计数）

**上下文**  
- `doy_sin`, `doy_cos`（替代裸 `month`）  
- `precip48_at_dawn`（沿用）  
- 地形/可观测场（沿用 v6）

**类比特征（Layer 2a，Phase 1b）**  
- `analog_pos_ratio`：前 k 个最相似标注日中 full/partial 占比  
- `analog_mean_label`：加权平均  
- `analog_dist_min`：与最近正样本的过程距离  

相似度：对 precursor 窗内 `(rh, cloud_low, cloud_mid, wind)`  hourly 曲线做 z-score 后欧氏距离，**仅限同 spot、同 month±1**。

### 4.3 训练 / 推理必须使用同一窗口

- **禁止**：训练用 03–07 实况、推理用 03–07 预报。  
- **必须**：训练用 **D-1 18:00 可获得的预报** 在 precursor 窗上的值；推理用 **当前时刻** 对目标日 D 的 live forecast 在同一窗上的值。

---

## 5. 数据层改造

### 5.1 新表：`meteo_forecast_archive`

| 字段 | 说明 |
|------|------|
| `spot_id`, `viewpoint_id` | 点位 |
| `target_date` | 标注日 D |
| `issue_time` | 预报发布时间（如 D-1 18:00） |
| `lead_hours` | 相对 issue 的 valid time |
| `source` | `historical_forecast` / `live_forecast` |
| `raw_json` | 单小时 meteorology（与现 `meteo_hourly` 结构一致） |

索引：`(spot_id, viewpoint_id, target_date, issue_time, ts)`。

### 5.2 回填策略

1. **历史标注日**：对每条 `cloudsea_labels`，调用 historical-forecast-api，`start_date=D-1`, `end_date=D`，存 **完整 48h precursor**；`issue_time` 记为 `D-1 18:00`（与 `backtest_key_days.advance_forecast` 一致）。  
2. **新标注**：`ensure_label_meteo_cached` 扩展为双写：  
   - `meteo_hourly`（事后真值，仅用于诊断/对比）  
   - `meteo_forecast_archive`（训练用）  
3. **脚本**：`scripts/backfill_forecast_archive.py` 批量回填已有标注。

### 5.3 标签扩展（可选，Phase 2）

在 `cloudsea_labels` 或旁表增加：

- `process_tag`：`building` / `dissipating` / `stable_clear`（标注员可选填，或从事后曲线半自动打）  
- 用于 hard negative：预报 dawn 高 RH 但 `process_tag=dissipating` 的 none 日

---

## 6. 模型层设计

### 6.1 Phase 1：过程特征 + 固定提前量重训（推荐先做）

- 扩展 `cloudsea_features.aggregate_precursor_features(hour_rows, window_spec)`  
- `train_cloudsea_model.py` 默认 `load_meteo_rows` → 读 `meteo_forecast_archive`  
- 特征名 `PRECURSOR_FEATURE_NAMES`，artifact version `cloudsea_ml_v7_precursor`  
- 评估：**必须**报告两套指标  
  - `oracle`：事后真值 precursor（上限）  
  - `operational`：D-1 18:00 预报 precursor（真实线上）

**成功标准（五女山试点）**  
- operational LOOCV F1 ≥ v6 + 5pp  
- 6/22、6/23 类相邻日 **不再同向错判**  
- 预报偏乐观日的 none 标签 **Recall 提升**

### 6.2 Phase 1b：历史类比模块（轻量、可解释）

- 无新训练：对每个推理日，在标注库中找 k=5 最近 precursor 曲线  
- 输出 `analog_pos_ratio` 作为特征或独立 **第二意见**  
- 若 `analog_pos_ratio < 0.2` 且 ML>55%，cap 到 45%

### 6.3 Phase 2：轨迹偏差校正（ML 参与「天气走向」）

**思路**：在标注日上学习「开源 dawn 预报 vs 事后 dawn 实况」的残差，或与是否出云海相关的 **系统偏差**。

- 目标变量（可多任务）：  
  - `Δrh_dawn` = actual_dawn.rh_mean − forecast_dawn.rh_mean  
  - `Δcloud_low_dawn`  
  - 或二分类 `forecast_overoptimistic`（dawn 预报 RH 比实况高 ≥10 且 labeled none）

- 输入：evening + night 的 **预报** 过程特征 + month/doy  
- 推理：先校正 dawn 段 RH/低云，再送入云海分类器  
- 数据需求：每条标注同时存 **forecast_archive + 事后 meteo_hourly**

### 6.4 Phase 3：序列模型（可选，样本量>200 再考虑）

- 输入：precursor 窗 11–12 个 hourly 向量  
- 模型：浅层 TCN / GRU 或 sklearn 的 flattened + L1 LR  
- 仅在 Phase 1/2 增益平台后尝试

---

## 7. 推理链改造（predictor）

1. 构建目标日 D 的 precursor 预报曲线（来自 live Open-Meteo）。  
2. 计算 `precursor_features` + `analog_features`（若启用）。  
3. （Phase 2）`corrected_dawn = forecast_dawn + residual_model.predict(...)`。  
4. `predict_day_cloudsea()` 使用 v7 特征；仅在 **dawn 小时** 与规则融合（保持现网 UX）。  
5. **新增 cap 规则**（与 ML 并行）：  
   - 若 `delta_rh_night_to_dawn < -5` 且 `cloud_low_mean_dawn < 25` → plausibility_cap ≤ 40  
   - 若 `analog_pos_ratio < 0.15` → cap ≤ 45  

---

## 8. 评估与验收协议

### 8.1 必跑脚本（每版模型）

| 脚本 | 用途 |
|------|------|
| `compare_ml_training.py --mode oracle\|operational` | LOOCV 对比 v6 vs v7 |
| `backtest_key_days.py --mode advance` | 固定提前量逐日表 |
| `eval_labeled_days.py` | 全量标注日方向一致率 |
| 新增 `eval_precursor_cases.py` | 专门输出 6/22–6/23 类「相邻日」diff |

### 8.2 报告字段

- 按 `month` 分层 F1 / Brier  
- **hard negative 子集**（labeled none 且 dawn 预报 rh_mean≥75）的 Recall  
- **false positive 子集**（labeled none 且 pred≥55%）逐日列表  
- oracle vs operational 差距（衡量对预报质量的依赖）

### 8.3 上线门槛

1. operational LOOCV 不差于 v6  
2. hard negative Recall 提升  
3. 至少 3 个「相邻日一有一无」case 方向正确  
4. 生产 shadow 运行 7 天（ML 只记日志不融合）后再开融合

---

## 9. 实施排期

| 阶段 | 内容 | 产出 | 工期估 |
|------|------|------|--------|
| **P0** | 数据：表结构 + 回填脚本 + 5 女山全量 archive | DB migration, backfill | 2–3 天 |
| **P1** | 特征：`aggregate_precursor_features` + 训练/推理切窗 | v7 模型 pkl | 2 天 |
| **P1b** | 类比 k-NN + cap 规则 | predictor 补丁 | 1 天 |
| **P2** | 偏差校正子模型 | residual pkl + 双阶段推理 | 3–4 天 |
| **P3** | 多点位 rollout + 文档 | 每 spot 独立 v7 | 持续 |

**建议路径**：P0 → P1 → 用 6/22–6/23 验收 → P1b → 再 P2。

---

## 10. 代码 touch 清单

| 模块 | 变更 |
|------|------|
| `backend/app/engine/cloudsea_features.py` | `aggregate_precursor_features`, 窗配置 |
| `backend/app/services/meteo_backfill.py` | 双写 archive + 真值 |
| `backend/app/services/cloudsea_store.py` | 新表 CRUD |
| `scripts/backfill_forecast_archive.py` | 新建 |
| `scripts/train_cloudsea_model.py` | `--issue-time`, `--window=precursor`, db 源切换 |
| `backend/app/engine/cloudsea_ml.py` | v7 artifact、analog 融合 |
| `backend/app/services/predictor.py` | precursor 窗 ML 输入、cap |
| `scripts/eval_precursor_cases.py` | 新建 |

---

## 11. 风险与边界

- **Open-Meteo historical-forecast** 是否完整支持「D-1 issue 的 D 日 dawn」需用 6/22–6/23 实测；若 API 仅给 reanalysis 型数据，需在文档中标注并改用「valid time 切片 + 模拟 lead」方案。  
- 样本少时 **勿堆深度模型**；过程特征 + 类比 + 校准更稳。  
- 偏差校正过拟合风险：必须 operational CV，不能用真值残差直接上线。  
- 规则引擎仍是安全网；ML 校正仅缩小 NWP 误差，不能替代物理 cap。

---

## 12. 与 6/22–6/23 案例的对照（预期改善）

| 日期 | 现象 | v6 可能行为 | v7 预期 |
|------|------|-------------|---------|
| 6/22 | 有日出云海 |  dawn 预报湿 → 中高概率 ✓ | 前夜–凌晨持续积湿 → 维持 |
| 6/23 | 无云海，云量低 |  dawn 预报仍偏湿 → 虚高 ✗ | night→dawn RH↓ / 低云低 + 类比 none + cap → 压低 |

---

*文档版本：2026-06 · 与 `ml-tuning-plan.md`（v6 tuned）并列，v7 实施完成后合并。*
