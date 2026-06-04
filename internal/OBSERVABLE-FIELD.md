# 可观测场（Observable Field）方案 · 已实施

> DEM 提供地形几何；观云模式定义「什么叫看到云海」；**可观测场**在二者之间，把「日出方向 × 能见度 × 各点成云潜力」算成可量化指标。

---

## 1. 概念

| 层 | 职责 |
|---|---|
| **DEM** | 海拔剖面、起伏、日出方位角 |
| **viewing_mode** | 判定策略：`valley_fill` vs `peak_overlook` |
| **observable_field** | 当前气象下，可见范围内多少地面「可填云」 |

## 2. 峰顶俯瞰（peak_overlook）定义

**可观测云海** = 在日出方位扇区（±45°）内、能见度可达距离内，满足：

- 地面海拔 < 观景点海拔
- 云底 < 该处地面海拔（该点可填云）
- 观景点在云上或云缘（非 `viewer_below_cloud_base`）

### 标注对齐

| 标注 | 含义 |
|---|---|
| **full** | 日出方向可见范围内，大面积谷地/坡地有清晰云海 |
| **partial** | 仅部分山谷/远端有云，或云薄/间断 |
| **none** | 可见方向均无观赏级云海（人在云下、全晴无云） |

## 3. 代码模块

| 文件 | 说明 |
|---|---|
| `backend/app/engine/solar.py` | 日出方位角 |
| `backend/app/adapters/dem.py` | `elev_profile_sunrise` 剖面采样 |
| `backend/app/engine/observable_field.py` | 可观测场计算 + 评分 |
| `backend/app/engine/cloudsea_scorer.py` | Type C + 可观测场因子 |
| `backend/app/engine/cloudsea_features.py` | ML v4 8 维可观测特征 |

## 4. API

```
GET /api/terrain/context?lat=&lng=&profile_date=YYYY-MM-DD
```

返回新增字段：

- `sunrise_azimuth_deg`
- `elev_profile_sunrise[]`（distance_km, azimuth_deg, elev_m）
- `elev_min_sunrise_15km_m`, `sunrise_sector_relief_m`

预测响应 `location.observable`：

- `observable_fraction`, `visible_range_km`, `fillable_points`, `note`

## 5. ML 训练

```bash
python scripts/train_cloudsea_model.py --db data/cloudsea/cloudsea.db --use-observable-field
python scripts/train_cloudsea_model.py --compare-terrain  # v2/v3/v4 LOOCV 对比
```

Artifact 版本：`cloudsea_ml_v4_observable`（38 维特征）

## 6. 评估

```bash
python scripts/eval_labeled_days.py --spot-id donglingshan --viewpoint-id fengding \
  --db data/cloudsea/cloudsea.prod.db
```

目标：东灵山 16 天方向一致率 > 44%（基线）
