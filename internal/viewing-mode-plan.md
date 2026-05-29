# 观云模式（Viewing Mode）分型方案

> 解决「每个社区/精选点位物理场景不同」——如五女山山谷填云 vs 东灵山峰顶俯瞰。  
> 状态：方案稿 · 待评审 · 未进代码

---

## 1. 问题

当前规则引擎与 Type A/B 型态来自 **五女山金标准**（中山坡、山谷云海、人在云缘或云内）。  
高海拔峰顶（东灵山 ~2300 m、黄山、庐山等）常见场景是：

- 观景点 **在云层之上**
- 能见度好，但 **脚下谷地** 有云海
- 模式 9 km 网格 + 单点云底公式 **无法表达**「看多远、云在哪一层」

因此：**不能全国一套规则 + 混训 ML**，需要 **点位观云模式 profile**。

---

## 2. 观云模式 taxonomy（初版）

| 模式 ID | 名称 | 典型点位 | 核心几何 | 观赏含义 |
|---------|------|----------|----------|----------|
| `valley_fill` | 山谷填云 | 五女山、大黑山（谷地型） | 云底低于峰顶，人站在坡/台地，云填满谷地 | 人在云里或云缘，Type A/B |
| `peak_overlook` | 峰顶俯瞰 | **东灵山**、泰山、黄山部分台 | **观景点海拔 > 云底/云顶**，云在下方 | 站在云海之上俯瞰 |
| `ridge_layer` | 山脊层云 | 部分鞍部、垭口 | 人与云同高，层云贴山脊 | 云带绕山 |
| `plateau_edge` | 高原/台地边缘 | 内蒙古部分高地 | 大范围低云，边缘清晰 | 边缘云海 |
| `coastal_advection` | 平流/海岸雾（扩展） | 辽东半岛部分 | 平流雾，非观赏云海 | 常 `fog_exclude` |

**默认策略：**

- 新建社区点：按 `elev_view` + `elev_max_5km` + `relief_5km` **粗猜**模式，允许贡献者/Admin 修改
- 精选 JSON / 社区库持久化 `viewing_mode`

---

## 3. 各模式规则差异（相对现有 `cloudsea_scorer`）

### 3.1 `valley_fill`（现状，五女山）

沿用现有逻辑：

- Type A：高能见度 + RH850 + 谷地湿润
- `_score_elevation_match`：人在 `[云底, 云底+800m]` 得分高
- 场景：「山谷云海」「人在云里谨慎前往」

### 3.2 `peak_overlook`（东灵山等 — **待实现**）

| 因子 | 与 valley_fill 的差异 |
|------|------------------------|
| 海拔匹配 | **加分**：`elev_view > cloud_top` 且 `cloud_base < elev_max_5km`（云在脚下谷地） |
| | **减分**：`cloud_base > elev_view`（人在云下，只能看天） |
| 能见度 | 峰顶 vis 好 **不应** 直接降分；看 **850/700 hPa 湿度 + 下方云量** |
| Type 判定 | 新增 Type C「峰顶俯瞰型」：中低层湿度高 + 谷地相对云高为正 |
| 场景标签 | `above_cloudsea`：站在云海之上 · 俯瞰 |
| 视野文案 | 结合 `horizon_elev` + 100 km 网格（Phase 2） |

**示意公式：**

```text
cloud_base_m  = estimate(T, Td)
cloud_top_m   = cloud_base + f(cloud_low, cloud_mid)
viewer_m      = elev_view
valley_peak_m = elev_max_5km

below_viewer  = cloud_top < viewer_m AND cloud_base < valley_peak_m   # 脚下有云海
in_cloud      = cloud_base < viewer_m < cloud_top                     # 人在云里（峰顶少见）
above_cloud   = viewer_m > cloud_top                                # 完全在云上，清晰俯瞰

peak_overlook_score ↑ when below_viewer AND vis_view good
```

### 3.3 `ridge_layer`

- 强调 `elev_view ≈ cloud_base ± 200m` + 沿脊 wind + 层云
- 权重介于 valley 与 peak 之间

### 3.4 降水 / 雾

所有模式共用：**日出窗口有雨 → 标注建议无云海，不进 ML**（已实现）。

---

## 4. 与 ML / 地形 / 区域产品的关系

```text
viewing_mode (profile)
      │
      ├──► 规则引擎分支（不同因子权重与场景）
      │
      ├──► ML 特征（云上小时数、cloud_base - valley_peak、…）
      │         └── 仍按点位 ≥30 有效日 单独 pkl（已实现框架）
      │
      └──► 100 km 网格（Phase 2：peak_overlook 更依赖「脚下格点 vs 头顶格点」）
```

| 模块 | 状态 |
|------|------|
| 分点位 ML + 30 日 + 降水排除 | ✅ 已上线 |
| `viewing_mode` 字段 | ⏳ 本方案 |
| DEM 相对高度 | ⏳ `terrain-cloudsea-plan` Phase 1 |
| Type C / peak_overlook 规则 | ⏳ 本方案 Phase A |
| 标注页选择观云模式 | ⏳ 本方案 Phase B |

---

## 5. 数据模型（拟议）

### 5.1 `community_locations` / 精选 JSON

```json
{
  "viewing_mode": "peak_overlook",
  "viewing_mode_source": "auto|contributor|admin",
  "elevation": 2303,
  "terrain_hint": {
    "elev_max_5km": 2100,
    "relief_5km": 1200
  }
}
```

### 5.2 自动推断（Phase A，无 DEM 时粗猜）

| 条件 | 建议模式 |
|------|----------|
| `elev_view >= 1800` 且 `elev_view - elev_max_5km >= 300` | `peak_overlook` |
| `relief_5km >= 400` 且 `elev_view < 1200` | `valley_fill` |
| 其他 | `valley_fill`（默认，与现网一致） |

有 DEM 后改用真实 `elev_max_5km` / `relief`（见 terrain plan）。

---

## 6. 分阶段实施

### Phase A · 规则分支（1–2 周）

- [ ] `viewing_mode` 枚举 + 社区点/精选 JSON 字段（默认 `valley_fill`）
- [ ] `peak_overlook` 评分分支（先用 Open-Meteo 云底 + 单点 elev，DEM 后补）
- [ ] 场景标签 `above_cloudsea`
- [ ] 预测/标注 API 返回 `viewing_mode`

### Phase B · 标注与运营（1 周）

- [ ] 标注页：显示/编辑观云模式（Admin 或贡献者）
- [ ] 东灵山等点位手工设为 `peak_overlook`，积累标注对比 valley 规则

### Phase C · 地形 + ML（与 terrain plan 合并）

- [ ] DEM 特征入规则与 `DAY_FEATURE_NAMES`
- [ ] 分模式统计 LOOCV（同一点位不应混两种模式的物理含义）

---

## 7. 东灵山示例（预期行为）

| 气象情景 | valley_fill（错误） | peak_overlook（目标） |
|----------|---------------------|------------------------|
| 峰顶晴、谷地湿、850hPa RH 高 | 低云量低 → 概率偏低 | 脚下可能有云海 → 概率中高 |
| 云底 1500 m，峰顶 2300 m | elevation_match 低 | **above_cloudsea** 加分 |
| 峰顶大雾、降水 | fog_exclude | 同左 |

---

## 8. 参考

- 现有：`backend/app/engine/cloudsea_scorer.py`（Type A/B、`_score_elevation_match`）
- 地形：[`terrain-cloudsea-plan.md`](terrain-cloudsea-plan.md)
- 标注/ML：[`CLOUDSEA-LABEL.md`](CLOUDSEA-LABEL.md)
- 山地云海 / 辐射雾 / 逆温层结：WMO 低云分类；业务上参考莉景「云高 vs 附近最高海拔」

---

## 9. 待你确认的问题

1. 社区点是否允许贡献者自选 `viewing_mode`，还是仅 Admin？
2. 东灵山是否已有社区点 / 需要预置精选 JSON？
3. Phase A 是否优先于 DEM 切片（可先用海拔粗猜 + 人工指定）？
