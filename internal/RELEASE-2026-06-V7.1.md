# 2026-06 补丁 · v7.1 贴地雾校准 & 漏报修复

> 发布日期：2026-06-24 · 热补丁部署（未修改 `.env` / `docker-compose.prod.yml`）

---

## 1. 升级摘要

在 **ML v7 + 预测反馈**（见 `RELEASE-2026-06-V7.md`）基础上，针对 6/24 虚高 case 与 5–6 月回测漏报进行规则/融合/模型迭代。

| 类别 | 内容 |
|------|------|
| **规则引擎** | 贴地雾 vs 观赏云海分型；Type A 季节放宽；5/20 饱和谷地云改判 |
| **ML 融合** | 贴地雾降权 valley_fog_boost；Type A/B 不被低 ML 过度拉低 |
| **产品** | D 日访问补全前体 evening；日出窗结束后冻结逐时 P |
| **ML v7.1** | 新特征 `ground_fog_proxy` / `type_a_proxy`；min_recall=0.68 重训 |

---

## 2. 回测效果（五女山 · 2026-05-01 ~ 06-30）

| 指标 | v7.0 | **v7.1** |
|------|------|----------|
| 方向一致（阈值 55%） | 83.3% (40/48) | **87.5% (42/48)** |
| 漏报 FN | 7 | **3** |
| 误报 FP | 1 | 3 |

**典型修复**

- **6/24**（标注 none）：访问快照曾 P≈70% → 回测 **P=20%**
- **5/20 / 5/29 / 6/06 / 6/22**：由漏报修复为命中

**仍待观察**

- FN：5/04、5/09、5/22（Type A · NWP 低云=0 金标准型）
- FP：5/12、5/26、6/08

---

## 3. 规则与融合变更

### 3.1 贴地雾代理 `is_ground_fog_proxy`

- 条件：NWP 低云 &lt;5% + 能见度 ≤500m + **RH≥82%**
- **水体雾例外**：近库信号 ≥0.3 且 RH&lt;82 时不判贴地雾（保留 5/29 类真云海）
- 命中后：不做低云补偿抬升；`fog_exclude`；融合 cap≈42%

### 3.2 Type A 金标准放宽

- 4–6 / 9–11 月 + 能见度 ≥5km + RH850≤55 时，湿度门槛 **50%**（原 68%）

### 3.3 5/20 类饱和谷地云

- `cloud_low≥40` 且 RH≥95、vis≤800 → **Type B**（不再 fog_exclude）

### 3.4 日出后概率冻结

- 目标日 07:00 后访问：03–06 点概率首次计算后冻结，避免能见度实况修正导致 P 回弹

### 3.5 前体窗补全

- `supplement_precursor_rows`：live 预报缺 D-1 20–23 时从 DB/archive 补全，保障 v7 特征完整

---

## 4. ML v7.1 模型

| 点位 | 样本 | LOOCV | 特征维 | 说明 |
|------|------|-------|--------|------|
| 五女山 | 66 | **83.3%** | 64 | +`ground_fog_proxy` +`type_a_proxy` +`night_dawn_cloud_low_gap` |
| 东灵山 | （沿用 v7 包） | 75.0% | — | 本次未重训 |

训练命令：

```bash
python3 scripts/train_cloudsea_model.py \
  --db data/cloudsea/cloudsea.prod.db \
  --spot-id wunvshan --viewpoint-id dianjiangtai \
  --window v7 --mode oracle --enhanced --db-only
```

---

## 5. 变更文件

| 路径 | 说明 |
|------|------|
| `backend/app/engine/cloudsea_scorer.py` | 贴地雾、Type A/B、5/20 改判 |
| `backend/app/engine/cloudsea_ml.py` | 融合降权、Type A/B 保护 |
| `backend/app/engine/cloudsea_features.py` | v7.1 特征 |
| `backend/app/services/predictor.py` | 前体补全、概率冻结 |
| `backend/app/services/meteo_backfill.py` | `supplement_precursor_rows` |
| `data/cloudsea/models/spot_wunvshan_dianjiangtai.pkl` | v7.1 权重 |
| `scripts/eval_period_backtest.py` | 区间回测 CLI |
| `scripts/analyze_prediction_snapshots.py` | 访问快照分析 |

---

## 6. 部署

```bash
SKIP_TRAIN=1 bash scripts/hot-patch-prod.sh
```

`hot-patch-prod.sh` 已纳入 `cloudsea_scorer.py`；训练默认改为 `--mode oracle`（operational archive 未齐时）。

---

## 7. 相关文档

- 上一版：`internal/RELEASE-2026-06-V7.md`
- 用户更新说明：`docs/docs/release-notes.html`
- 6/24 个案分析：对话记录 / `scripts/analyze_prediction_snapshots.py`

---

*维护者：timkj · 2026-06-24*
