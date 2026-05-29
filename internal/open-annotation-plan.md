# 开放云海标注 · 产品化实施计划

> 版本：v0.1 · 待评审  
> 目标：开放标注工具，支持 POI/社区点位；无需申请 Contributor Token；标注经审核后进入训练与精选落库；主页提示 + 收藏深链直达预测页。

---

## 1. 背景与目标

### 1.1 现状

| 已有 | 缺口 |
|------|------|
| `label.html` + 三档云海标注 UI | 仅 Admin Token，非公开 |
| `cloudsea.db` + ML 训练脚本 | 仅五女山 curated 观景点 |
| POI 选点 + `/api/predict` | 标注页不支持 POI/自定义坐标 |
| 五女山 32 日标注 + v2 模型 | 无审核、无社区点位、无深链 |

### 1.2 产品目标

1. **零门槛进入**：打开标注页即可用，无需填 Token、无需申请表单。
2. **POI 可标注**：从主页 POI 选点或地图微调后，一键进入标注流程。
3. **可控质量**：限流 + 审核后才进训练集，防止注水。
4. **可运营**：标注达标点位可精选落库；模型定期重训；主页 Banner 引导。
5. **可收藏**：浏览器收藏 URL 直达某点位预测页或标注页。

### 1.3 非目标（本期不做）

- 用户注册 / 登录 / 手机号
- 日出 ML 训练（UI 可预留，训练 Phase 2）
- 标注附件上传（照片证据）
- 多人标注冲突自动仲裁（先人工审核）

---

## 2. 身份识别：浏览器唯一 ID（替代 Contributor 申请）

### 2.1 为什么不用「机器码」

Web 应用**无法可靠读取硬件机器码**（OS/浏览器安全限制）。可行替代：

| 方案 | 持久性 | 说明 |
|------|--------|------|
| **localStorage UUID（推荐主键）** | 清缓存前一直有效 | 首次访问生成 `yunhai_cid`，永久保存 |
| **sessionStorage UUID** | 仅当前标签页 | 不适合跨会话标注 |
| 浏览器指纹（FingerprintJS 等） | 较稳定但可漂移 | 仅作辅助风控，不作主键 |
| IP 地址 | 易变（移动网络/NAT） | 辅助限流，不作唯一身份 |

**产品化结论**：用户无感知，第一次打开站点/标注页自动生成 Contributor ID，后续自动携带。

### 2.2 Contributor ID 生成规则

```
格式：cid_<uuid-v4>
存储：localStorage['yunhai_contributor_id']
生命周期：永久（除非用户清站点数据）
```

前端逻辑（主页 + 标注页共用 `contributor.ts`）：

1. 读取 `localStorage`；无则 `crypto.randomUUID()` 生成并写入。
2. 所有开放标注 API 携带 Header：`X-Contributor-Id: cid_xxx`。
3. 标注页展示只读 ID 后 8 位，便于用户反馈问题时引用（可选）。

### 2.3 服务端登记

首次提交标注时，服务端 `UPSERT contributors` 表：

| 字段 | 说明 |
|------|------|
| `id` | `cid_xxx` |
| `first_seen_at` | 首次请求时间 |
| `last_seen_at` | 最近活跃 |
| `label_count_total` | 累计提交数 |
| `label_count_approved` | 审核通过数 |
| `trust_level` | `new` / `regular` / `trusted`（自动升级，见 §4） |
| `blocked` | 是否封禁 |

**无需申请流程**；滥用时 Admin 按 `contributor_id` 或 IP 封禁。

### 2.4 隐私说明（主页/标注页底部一行）

> 为限制恶意刷标注，本工具会在浏览器本地生成匿名 ID 并随标注提交，不包含姓名、手机号或精确设备信息。清除站点数据后将生成新 ID。

---

## 3. 限流与防刷（按你的建议调整）

### 3.1 核心限额

| 规则 | 值 | 说明 |
|------|-----|------|
| **每 Contributor 每日标注上限** | **30 条/自然日（Asia/Shanghai）** | 按「成功 upsert 的 label 条数」计，含修改同一天 |
| 每 Contributor 注册社区点位 | 10 个 | 超出返回 429 |
| 同坐标去重半径 | 500 m | 禁止重复注册近似点 |
| 可标注日期范围 | 今天及以前 | 禁止未来日期 |
| 同点位同日期 | 1 条 | UNIQUE 约束，修改不计新增 |
| IP 辅助限流 | 200 次/小时/标注 API | 防脚本轰炸 |

> **30 条/日**：适合集中补标历史月份；若发现刷量，Admin 可下调单个 contributor 或全局配置 `CLOUDSEA_DAILY_LABEL_CAP`。

### 3.2 训练入库门槛（与限流分离）

| 规则 | 值 |
|------|-----|
| 进入训练集 | 仅 `review_status = approved` |
| 单点最低训练样本 | 10 天 approved（含正负样本） |
| 全局重训触发 | approved 总数增量 ≥20 **或** 距上次重训 ≥30 天 |

未审核 / 驳回的标注**永不进模型**。

### 3.3 信任等级（自动，无需申请）

| 等级 | 条件 | 权益 |
|------|------|------|
| `new` | 默认 | 正常限流 |
| `regular` | ≥10 条 approved | 社区点注册上限 15 |
| `trusted` | ≥30 条 approved 且驳回率 <10% | 标注默认 `pending` 仍可展示贡献榜；可选「快速通道」直接 approved（可配置关闭） |

---

## 4. 数据模型

### 4.1 新表：`community_locations`

POI / 用户自定义点的持久化实体（curated 景区仍用 JSON，不迁入此表）。

```sql
CREATE TABLE community_locations (
  id TEXT PRIMARY KEY,           -- cs_<shortid>
  slug TEXT UNIQUE,              -- 可选，URL 友好名 huangyakou
  name TEXT NOT NULL,
  lat REAL NOT NULL,
  lng REAL NOT NULL,
  elevation REAL,
  contributor_id TEXT NOT NULL,
  source TEXT NOT NULL,          -- poi | map_click | import
  status TEXT NOT NULL DEFAULT 'active',  -- active | merged | hidden
  review_status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  label_count INTEGER DEFAULT 0,
  approved_label_count INTEGER DEFAULT 0,
  curated_spot_id TEXT,          -- 精选落库后指向 scenic-spots id
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_community_loc_contrib ON community_locations(contributor_id);
CREATE INDEX idx_community_loc_geo ON community_locations(lat, lng);
```

**ID 生成**：`cs_` + base62(8) 或 uuid 前 8 位，保证深链短且唯一。

### 4.2 扩展：`cloudsea_labels`

```sql
ALTER TABLE cloudsea_labels ADD COLUMN location_id TEXT;  -- cs_xxx 或 NULL（curated 用 spot+vp）
ALTER TABLE cloudsea_labels ADD COLUMN lat REAL;
ALTER TABLE cloudsea_labels ADD COLUMN lng REAL;
ALTER TABLE cloudsea_labels ADD COLUMN location_name TEXT;
ALTER TABLE cloudsea_labels ADD COLUMN contributor_id TEXT;
ALTER TABLE cloudsea_labels ADD COLUMN review_status TEXT DEFAULT 'pending';
ALTER TABLE cloudsea_labels ADD COLUMN reviewed_at TEXT;
ALTER TABLE cloudsea_labels ADD COLUMN reviewed_by TEXT;

-- 兼容：curated 仍 (spot_id, viewpoint_id, date)
-- 社区点：(location_id, date) 或 (contributor_id, lat, lng, date) 去重
```

**review_status**：`pending` | `approved` | `rejected`

### 4.3 新表：`contributors`

见 §2.3。

### 4.4 新表：`model_train_runs`（可选，运营可见）

记录每次重训：版本号、样本数、LOOCV 准确率、是否已部署。

---

## 5. API 设计

### 5.1 路由分层

| 类型 | 前缀 | 鉴权 |
|------|------|------|
| 公开标注 | `/api/contribute/cloudsea/*` | `X-Contributor-Id` + 限流 |
| 管理审核 | `/api/internal/cloudsea/*` | 现有 `X-Cloudsea-Token`（Admin） |
| 公开只读 | `/api/contribute/locations/{id}` | 无 |

### 5.2 公开 API 列表

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/contribute/locations` | 注册社区点位（POI/地图点） |
| GET | `/api/contribute/locations/mine` | 我的点位列表 |
| GET | `/api/contribute/cloudsea/label-session` | 加载标注会话（支持 `location_id` 或 `lat/lng`） |
| POST | `/api/contribute/cloudsea/labels` | 提交/更新标注 |
| GET | `/api/contribute/cloudsea/calendar` | 月历 |
| GET | `/api/contribute/cloudsea/stats` | 我的贡献统计（已提交/已通过/待审） |

**label-session 参数（三选一）**：

```
A) spot_id + viewpoint_id          → curated 景区
B) location_id=cs_xxx              → 社区点位
C) lat + lng + name + elevation?   → 临时 POI（自动 register 或复用 500m 内已有点）
```

### 5.3 Admin API 扩展

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/internal/cloudsea/review-queue` | 待审标注 + 点位 |
| POST | `/api/internal/cloudsea/labels/{id}/review` | approve / reject |
| POST | `/api/internal/cloudsea/locations/{id}/curate` | 精选落库 → 写 scenic-spots JSON |
| POST | `/api/internal/cloudsea/train` | 手动触发重训（返回 LOOCV 报告） |
| POST | `/api/internal/contributors/{id}/block` | 封禁 |

---

## 6. 前端改造

### 6.1 共用模块 `contributor.ts`

- `getContributorId()`：读/写 localStorage
- `contributorHeaders()`：注入 `X-Contributor-Id`

### 6.2 主页 `App.vue`

- **Banner**（可关闭，localStorage 记 `banner_dismissed`）：
  > 云海标注已开放：贡献你的观测，帮助改进模型 · [去标注]
- **深链解析**（`onMounted`）：
  - `?spot=wunvshan&vp=yunhaisongtao`
  - `?loc=cs_a1b2c3`
  - `?lat=40.633&lng=122.509&name=黄丫口`
- POI 预测面板增加按钮：**「标注此点位」** → `/label.html?lat=...&lng=...&name=...`

### 6.3 标注页 `CloudseaLabelTool.vue`

| 改动 | 说明 |
|------|------|
| 移除 Admin Token 必填 | 改为自动 Contributor ID；Admin 模式保留（URL `?admin=1` + Token，显示审核队列） |
| 点位来源 | Tab：精选景区 / 我的社区点 / 从 URL 带入 POI |
| 提交反馈 | 显示「已提交，待审核」；月历区分 pending/approved 颜色 |
| 贡献统计 | 底部：今日已用 x/30、累计通过 y 条 |

### 6.4 收藏 URL 规范

| 场景 | URL |
|------|-----|
| 预测 · 精选 | `https://yunhai.timkj.com/?spot=wunvshan&vp=yunhaisongtao` |
| 预测 · 社区 | `https://yunhai.timkj.com/?loc=cs_a1b2c3` |
| 预测 · POI 坐标 | `https://yunhai.timkj.com/?lat=40.633&lng=122.509&name=黄丫口` |
| 标注 · 社区点 | `https://yunhai.timkj.com/label.html?loc=cs_a1b2c3&date=2026-05-29` |
| 标注 · POI | `https://yunhai.timkj.com/label.html?lat=40.633&lng=122.509&name=黄丫口` |

---

## 7. 审核与精选落库

### 7.1 审核流程

```
用户提交 → review_status=pending
    ↓
Admin 看板（现有 analytics 风格或 label.html admin 模式）
    ↓
approve → 计入 approved_label_count；达阈值可触发训练评估
reject  → 不计入训练；可选填写原因
```

**批量审核**：按 contributor、按 location、按月份筛选。

### 7.2 精选落库条件（建议默认）

| 条件 | 阈值 |
|------|------|
| approved 标注天数 | ≥15 |
| 正负样本 | 至少 3 天 none + 3 天 partial/full |
| 点位 review | location `approved` |
| 人工确认 | Admin 点击「精选落库」 |

**落库动作**：

1. 生成 `data/scenic-spots/{id}.json`（单观景点，tags 含 `cloudsea`, `community`）
2. 更新 `community_locations.curated_spot_id`
3. 重启或热加载 spot_loader（当前启动时加载，需 restart 或加 reload API）
4. 主页搜索出现「社区精选 · 黄丫口」

### 7.3 ML 权重

精选落库后，该 `spot_id` 在 `ml_calibration_weight()` 中按「其他内置景区」55% 处理；若标注 ≥30 天且 LOOCV 分 spot 表现好，可升为 65%。

---

## 8. 模型定期重训

### 8.1 训练数据

```python
# train_cloudsea_model.py 改动要点
labels = load_labels(review_status='approved')  # 含 curated + community
features keyed by (lat, lng, elevation) or location_id
```

### 8.2 触发方式

| 方式 | 说明 |
|------|------|
| **手动** | Admin `POST /api/internal/cloudsea/train` |
| **定时** | 服务器 cron 每月 1 日 03:00，或 approved 增量 ≥20 |
| **部署门禁** | LOOCV accuracy ≥ 当前 v2 基线（~72%）才替换 pkl |

### 8.3 上线后展示

- 主页 Footer 或关于页：`模型 v3 · 基于 N 条社区标注 · 更新于 YYYY-MM-DD`
- 标注页：「你的标注将在审核通过后纳入下次训练（预计每月初）」

---

## 9. 配置项（环境变量）

```env
# 开放标注
CLOUDSEA_CONTRIBUTE_ENABLED=true
CLOUDSEA_DAILY_LABEL_CAP=30          # 每 contributor 每日上限
CLOUDSEA_MAX_LOCATIONS_PER_CONTRIBUTOR=10
CLOUDSEA_DEDUP_RADIUS_M=500
CLOUDSEA_AUTO_APPROVE_TRUSTED=false    # trusted 是否免审（默认关）

# 训练
CLOUDSEA_TRAIN_MIN_APPROVED=20
CLOUDSEA_TRAIN_MIN_SPOT_LABELS=10
CLOUDSEA_MODEL_MIN_LOOCV=0.70

# Admin 不变
CLOUDSEA_ADMIN_TOKEN=...
CLOUDSEA_ENABLED=true
```

---

## 10. 实施分期

### Phase 1 · 开放标注 MVP（约 4–5 天）

- [ ] `contributor.ts` + `contributors` 表
- [ ] 公开 API：`/api/contribute/cloudsea/*` + 30/日限流
- [ ] `community_locations` + POI 注册与 500m 去重
- [ ] `label-session` / `labels` 支持 location_id 与 lat/lng
- [ ] 标注页去 Token 化、接 Contributor ID
- [ ] 主页 Banner + 深链 + 「标注此点位」

**验收**：POI 选黄丫口 → 标注昨日 → pending 入库；同一浏览器次日可继续标；第 31 条返回 429。

### Phase 2 · 审核与训练（约 2–3 天）

- [ ] Admin 审核队列 UI（或 internal 页）
- [ ] `review_status` 流转
- [ ] `train_cloudsea_model.py` 只读 approved + 多点位
- [ ] 手动重训 + LOOCV 报告

**验收**：驳回标注不进训练；approve 20 条后可出 v3 报告。

### Phase 3 · 精选落库与运营（约 2 天）

- [ ] `curate` → scenic-spots JSON
- [ ] 搜索展示社区精选
- [ ] 模型版本与贡献数展示
- [ ] cron 定时重训（可选）

**验收**：黄丫口 15 天 approved → 落库 → 主页可搜到 → 收藏深链直达。

### Phase 4 · 增强（可选）

- [ ] 日出标注 UI + 表结构
- [ ] 贡献排行榜 / 点位标注进度
- [ ] 标注页嵌入卫星云图辅助判断

---

## 11. 风险与对策

| 风险 | 对策 |
|------|------|
| 清缓存换 ID 绕过限流 | IP 辅助限流；同内容重复 pending 合并；异常模式 Admin 封禁 |
| 恶意刷 pending 撑爆 DB | 每 contributor pending 上限 100；自动归档 90 天前 rejected |
| 多设备同一人 | 接受；质量靠审核，不靠身份唯一 |
| 社区点与 curated 重名 | slug 冲突检测；展示加「社区」角标 |
| 训练被脏数据拉低 | 仅 approved；LOOCV 门禁；保留 v2 回滚 |
| 精选落库后坐标不准 | 落库前 Admin 地图确认；支持 JSON 手调 |

---

## 12. 待你确认的问题

1. **30 条/日**：是否含「修改同一天标注」？建议：**含**（upsert 算 1 条，但计 daily quota 仅首次创建该Date时 +1，修改不额外消耗）。
2. **trusted 免审**：默认 **关闭**；是否同意？
3. **POI 临时点**：首次标注是否自动创建 `community_locations`？建议：**是**，避免重复 lat/lng 键混乱。
4. **Banner**：是否允许用户永久关闭？建议：**是**（localStorage）。
5. **Admin 审核入口**：继续用 `label.html?admin=1` + Token，还是单独 `internal/review.html`？建议：**label.html admin 模式**（少维护一套 UI）。

---

## 13. 总结

| 你的诉求 | 方案 |
|----------|------|
| 不想申请 Contributor | **localStorage 匿名 ID**，零表单 |
| 每日 30 条上限 | `CLOUDSEA_DAILY_LABEL_CAP=30`，按自然日 Shanghai |
| POI 可标注 | `community_locations` + label-session 支持 lat/lng |
| 防注水 | pending 审核 + 训练只用 approved + IP/配额 |
| 产品化 | Banner、深链、精选落库、定期重训、模型版本展示 |

**整体可行，建议按 Phase 1 → 2 → 3 上线；Phase 1 结束即可对外软开放收集标注。**

---

*文档路径：`internal/open-annotation-plan.md`*
