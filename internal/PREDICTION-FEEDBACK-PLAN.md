# 预测访问快照 · 次日回测 · 标注页分析工具

> 目标：用户每次查看预测时落库「当时的气象 + 点位 + 预测结果」；日出日结束后用完整天气与标注回测；在标注页展示历史预测与实况差异，导出数据供 ML 迭代。  
> 状态：方案 · 待实施（用户已回撤 advance_20h 实验代码，本方案独立）

---

## 1. 能不能做到？

**可以。** 与现有能力高度契合：

| 已有 | 用途 |
|------|------|
| `run_prediction` / `build_predictions_from_hourly` | 线上预测链完整 |
| `meteo_backfill` + `meteo_forecast_archive` | 事后完整天气、固定 issue 预报 |
| `cloudsea_labels` | 日出日真值 |
| `prediction_runs` | 仅 backtest 内部用，字段过少，需新表 |
| `CloudseaLabelTool.vue` | 标注页壳，可嵌「历史预测」面板 |
| `analytics_store` | 页面 PV，**无**气象/预测，不替代本方案 |

交付给你 CSV/SQLite 导出后，可专门做：虚高 case 聚类、按 lead_time 分层 retrain、cap 规则验证。

---

## 2. 产品定义

### 2.1 记录什么（访问瞬间）

用户（或标注员）在 **任意时刻 T** 打开某点位、查看 **目标日出日 D** 的预测时，写入一条 **访问快照**：

```
issue_time = T（北京时间）
target_date = D（日出日）
lead_hours = (D 03:00) - T  （到日出窗起点的小时数，可负表示已过窗）
```

| 字段组 | 内容 |
|--------|------|
| 点位 | spot_id, viewpoint_id, lat, lng, elevation, location_id |
| 预测 | 日概率、分级、ML/规则分、逐小时表、factors、model_version |
| 气象快照 | **当时 live forecast** 在 relevant 窗的 hourly（见下） |
| 元数据 | data_source, client_id(yunhai_cid), page(referer), user_agent |

**relevant 窗（与 V2 对齐）**：

- 若 T ∈ [D-1 20:00, D 12:00)：存 **T 可见的** forecast 序列  
  - 已过去时段：可存 forecast 或短历史（实现阶段二选一，需标注 `snapshot_kind`）  
  - 未到来时段：forecast，重点 **D 03–07（或 04–07）**  
- 最小集：precursor 12h（D-1 20:00 → D 07:00）+ 可选延伸至 D 12:00

### 2.2 次日回测什么（D 日结束后）

定时任务（如 D+1 06:00）对 `target_date = D` 且已有标注或已过日出窗的快照：

| 回填 | 来源 |
|------|------|
| `label_status` | `cloudsea_labels` |
| `actual_meteo` | `meteo_hourly` 全日 + precursor 窗 |
| `forecast_vs_actual` | 按 valid_time 对齐：RH、低云、风、vis 差值；分段 evening/night/dawn |
| `outcome` | pred≥50% vs label；方向是否正确 |
| `diagnosis_tags` | 规则打标：`overoptimistic_dawn`, `night_dried`, `process_mismatch` 等 |

### 2.3 标注页展示什么

选中 **点位 + 标注日 D** 时，侧边/下方展示：

1. **时间轴**：该点位 D 日前所有用户访问（20:00 前一日 → 当日中午）  
   - 每次访问：时间、P(云海)、ML/规则、lead_hours  
2. **气象演变曲线**（双轨）：  
   - 灰线：访问时刻的 forecast（该次 snapshot）  
   - 实线：D 日事后完整数据（oracle）  
   - 分 RH / 低云 / 风 子图  
3. **结果条**：标注 full/partial/none vs 各次预测 ✓/✗  
4. **差异摘要**（自动）：如「6/23 03:00 预报 RH 83%，实况 71%；night→dawn ΔRH 预报 +3、实况 -8」

---

## 3. 数据模型（建议新表）

### 3.1 `prediction_access_log`

```sql
CREATE TABLE prediction_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,           -- issue_time ISO
    target_date TEXT NOT NULL,
    lead_hours_to_dawn REAL,
    spot_id TEXT,
    viewpoint_id TEXT,
    location_id TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    elevation REAL,
    page_source TEXT,                   -- main | label | share | api
    client_id TEXT,
    model_version TEXT,
    data_source TEXT,                   -- live_forecast | historical_forecast
    prediction_json TEXT NOT NULL,      -- PredictResponse 精简版
    meteo_snapshot_json TEXT NOT NULL,  -- hourly rows + window_spec
    feature_snapshot_json TEXT,         -- 可选：v7 特征向量，便于离线分析
    UNIQUE NULL                         -- 允许多次访问
);
CREATE INDEX idx_pal_spot_date ON prediction_access_log(spot_id, viewpoint_id, target_date);
CREATE INDEX idx_pal_created ON prediction_access_log(created_at);
```

### 3.2 `prediction_access_outcome`

```sql
CREATE TABLE prediction_access_outcome (
    access_log_id INTEGER PRIMARY KEY REFERENCES prediction_access_log(id),
    reconciled_at TEXT NOT NULL,
    label_status TEXT,
    label_id INTEGER,
    actual_meteo_json TEXT,
    forecast_error_json TEXT,           -- 分段/逐时残差
    predicted_positive INTEGER,       -- 0/1
    label_positive INTEGER,
    direction_ok INTEGER,
    diagnosis_json TEXT               -- tags + 可读摘要
);
```

与现有 `prediction_runs`：**保留**给 internal backtest；新表面向 **全量用户访问**。

---

## 4. 系统架构

```
用户打开预测页 /api/predict
        │
        ▼
  run_prediction() ──async──► insert prediction_access_log
        │
        │（D+1 定时）
        ▼
  reconcile_outcomes.py
    · 拉 label + meteo_hourly 全日
    · 算 forecast vs actual
    · 写 prediction_access_outcome
        │
        ▼
  标注页 GET /api/internal/cloudsea/prediction-history?spot&date
        │
        ▼
  CloudseaLabelTool 面板：时间轴 + 双轨曲线 + 差异表
        │
        ▼
  导出 scripts/export_prediction_feedback.py → 交付分析 / ML
```

**写入原则**：异步线程/队列，失败不阻塞预测；体积控制：meteo 只存 relevant 窗 ~12–16 小时 × 变量集。

---

## 5. 分析能力（为何能提升准确率）

| 分析维度 | 用途 |
|----------|------|
| 按 lead_hours 分层准确率 | 20:00 vs 04:00 查看，决定产品提示「早看/晚看」 |
| forecast vs actual 残差分布 | 训练偏差校正子模型（V2 Phase 2） |
| 虚高 case 的过程标签 | 自动打 `dissipating` → cap 规则 |
| 同点位多次访问预测漂移 | 检验 live forecast 更新是否改善 |
| 与标注日相邻对（6/22–6/23） | 专用 case 库 |

**你导出数据后我可做**：

1. 统计哪类 `diagnosis_tag` 占 false positive  
2. 是否应对「20 点访问 + 4–7 点预报」单独训一版  
3. 建议 cap / 类比阈值  
4. 增量标注：优先补「高访问量但错判」的日期  

---

## 6. 实施排期

| 阶段 | 内容 | 工期估 |
|------|------|--------|
| **P0** | 表结构 + `predict` 钩子写 log + 导出脚本 | 2 天 |
| **P1** | `reconcile_outcomes` 定时任务 + diagnosis 规则 | 2 天 |
| **P2** | 标注页 API + 历史预测/实况对比 UI | 3 天 |
| **P3** | 独立内部分析页（可选，复用 analytics 鉴权） | 2 天 |
| **P4** | ML 反馈闭环（导出 → 重训 → shadow） | 持续 |

**建议先做 P0+P1**，本地/Staging 跑 2 周积数据，再开发标注页 UI。

---

## 7. API 草案

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | （内嵌于 `/api/predict`） | 自动写 log，无需前端改 |
| GET | `/api/internal/cloudsea/prediction-history` | spot, date, 返回 snapshots + outcomes |
| GET | `/api/internal/cloudsea/prediction-history/{id}` | 单次访问详情 + 曲线数据 |
| POST | `/api/internal/cloudsea/reconcile` | 手动触发某日回测（Admin） |
| GET | `/api/internal/cloudsea/export/feedback` | CSV/JSON 导出 |

---

## 8. 标注页 UI 线框（P2）

```
┌─ 标注日 2026-06-23 ─────────────────────────────┐
│ 标注：none  │  用户预测 3 次  │  正确 0/3        │
├─────────────────────────────────────────────────┤
│ [访问列表]                                       │
│  6/22 21:04  P=68%  lead=6h   ✗                │
│  6/23 04:12  P=71%  lead=0h   ✗                │
│  6/23 05:30  P=65%  lead=-2h  ✗                │
├─────────────────────────────────────────────────┤
│ [RH 演变]  — forecast@21:04 ··· 实况 ───        │
│ [低云]     — forecast ··· 实况 ───              │
├─────────────────────────────────────────────────┤
│ 差异：night→dawn ΔRH 预报+3 实况-8 → dissipating │
└─────────────────────────────────────────────────┘
```

---

## 9. 隐私与体积

- 不存 IP 明文（可选 hash）；client_id 已有 cid 机制  
- 保留 90 天（与 analytics 一致，可配置）  
- 单条 snapshot ~5–15KB；1000 次/月 ≈ 15MB，可接受  

---

## 10. 与 V2 ML 的关系

- **不替代** 标注日训练集；是 **线上行为 + 预报时效** 的补充信号  
- 回测后的 `forecast_error_json` 可直接 feed `meteo_forecast_archive` 质量评估  
- 待数据足够：用「访问时刻 issue 的 forecast + Outcome」做 **operational LOOCV 的外推验证**（比实验室 LOOCV 更接近真实）

---

*文档版本：2026-06 · 待用户确认后从 P0 实施。*
