# 云海标注工具

## 开放标注（用户）

1. 打开：**https://yunhai.timkj.com/label.html**（无需 Token）
2. 或从主页 POI 选点后点击 **「标注此点位」**
3. 选择日期：
   - **云海**：按 `1` / `2` / `3` 标注无云海 / 部分 / 完整
   - **日出质量**（可选）：可见 / 遮挡 / 不可拍（需先选云海档位）
4. 保存后 **立即生效**（社区点位免审核）；首次标注会自动 **加入精选**，主页搜索即可直达

### POI → 社区点位流程

```
主页搜索 POI → 选点预测 → 「标注此点位」
    → 首次保存自动创建 cs_xxx 社区点
    → 首次标注即写入精选景区（主页可搜到）
    → 云海标注直接进入 ML 训练集（社区点免审核）
    → 日出质量写入数据库，供后续日出 ML（当前重训不使用）
```

### 编辑社区点位（持久化）

在标注页选择 **社区 / POI** 模式下「我的」点位后，可编辑：

- 名称、纬度、经度、海拔（WGS84）
- `PATCH /api/contribute/locations/{id}`（仅创建者本人）
- 若该点已 **落库精选**，保存后同步更新 `scenic-spots/*.json` 与主页预测坐标

收藏链接：

- 预测：`/?loc=cs_xxx` 或 `/?lat=40.48&lng=122.98&name=黄丫口`
- 标注：`/label.html?loc=cs_xxx&date=2026-05-29`

---

## 标注字段与 ML 关系

| 字段 | 取值 | 是否进入当前云海 ML |
|------|------|---------------------|
| `status` | none / partial / full | ✅ 是（需 **approved** 且 **日出窗口无降水**） |
| `sunrise_quality` | visible / blocked / unshootable | ❌ 否（先积累样本，中期单独训练日出模型） |

### ML 启用规则（按点位）

- 每个观景点/社区点需 **≥30 天有效标注** 才可训练并启用 **专属 ML**（Admin 重训后生效）。
- **有效标注** = 已审核通过 + 03:00–07:00 气象完整 + **该窗口内无降水**（≥0.1 mm/h）。
- 未达 30 天或尚无本点位模型时：**03–07 点仅规则引擎**，标注页与预测页会提示。
- 五女山使用全局 `cloudsea_ml_v2.pkl`（达标后）；社区点使用 `spot_community_cs_xxx.pkl`，**不会**混用五女山模型。

### 降水日标注

- 若日出窗口内有雨，标注页会 **红色提示**，建议直接标「无云海」。
- 降水日仍可保存标注（作记录），但 **不计入 ML 训练**，也不计入 30 日达标计数。

Admin **重训 ML** 按点位分别训练（排除降水日）；仍只使用 `status`。日出质量达标后计划新增 `train_sunrise_model.py`。

---

## Admin 审核（维护者）

打开：**https://yunhai.timkj.com/label.html?admin=1**

1. 填入 **Admin Token**（与服务器 `CLOUDSEA_ADMIN_TOKEN` 相同）→ 保存
2. 页面下方 **「Admin · 审核与训练」** 面板：
   - **刷新队列**：查看所有 pending 标注
   - **通过 / 驳回**：逐条或批量处理
   - 点击条目可跳转查看该日气象与标注详情
   - **落库**：社区点 ≥1 条标注后自动落库；也可手动触发
   - **重训 ML**：仅用 approved 的 **云海 status** 训练；LOOCV 达标后需手动部署新 pkl

---

## 30 天标注是什么意思？

ML 训练需要 **30 个有效标注日**（日出窗口 03:00–07:00 各标一次云海），含正负样本。

**有效** = 已审核 + 日出窗口 **无降水** + 气象字段完整。降水日建议标「无云海」，但不计入有效样本。

建议：有云海 10–15 天 + 无云海 15–20 天（均为无雨日）。日出质量可同步标注，便于日后训练。

---

## 快捷键

- `1` / `2` / `3`：无云海 / 部分 / 完整
- `←` / `→`：上一天 / 下一天

---

## 本地开发

```bash
cd backend && CLOUDSEA_ENABLED=true CLOUDSEA_CONTRIBUTE_ENABLED=true CLOUDSEA_ADMIN_TOKEN=dev-token uvicorn app.main:app --reload
cd frontend && npm run dev
# http://127.0.0.1:5173/label.html
# http://127.0.0.1:5173/label.html?admin=1
```

---

## 训练与部署

```bash
python3 scripts/train_cloudsea_model.py --approved-only
# 或 Admin 页「重训 ML」按钮
# 注意：仅使用 status 字段，不含 sunrise_quality

# 部署：更新 CLOUDSEA_MODEL_PATH 并重启容器
```

详见 [`internal/open-annotation-plan.md`](open-annotation-plan.md)
