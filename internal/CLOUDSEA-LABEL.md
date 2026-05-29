# 云海标注工具

## 开放标注（用户）

1. 打开：**https://yunhai.timkj.com/label.html**（无需 Token）
2. 或从主页 POI 选点后点击 **「标注此点位」**
3. 选择日期，按 **1 / 2 / 3** 标注无云海 / 部分 / 完整
4. 保存后 **立即生效**（社区点位免审核）；首次标注会自动 **加入精选**，主页搜索即可直达，无需重复选 POI

### POI → 社区点位流程

```
主页搜索 POI → 选点预测 → 「标注此点位」
    → 首次保存自动创建 cs_xxx 社区点
    → 首次标注即写入精选景区（主页可搜到）
    → 标注直接进入 ML 训练集（社区点免审核）
```

收藏链接：

- 预测：`/?loc=cs_xxx` 或 `/?lat=40.48&lng=122.98&name=黄丫口`
- 标注：`/label.html?loc=cs_xxx&date=2026-05-29`

---

## Admin 审核（维护者）

打开：**https://yunhai.timkj.com/label.html?admin=1**

1. 填入 **Admin Token**（与服务器 `CLOUDSEA_ADMIN_TOKEN` 相同）→ 保存
2. 页面下方 **「Admin · 审核与训练」** 面板：
   - **刷新队列**：查看所有 pending 标注
   - **通过 / 驳回**：逐条或批量处理
   - 点击条目可跳转查看该日气象与标注详情
   - **落库**：社区点 ≥1 条标注后自动落库；也可手动触发
   - **重训 ML**：仅用 approved 样本训练；LOOCV 达标后需手动部署新 pkl

---

## 30 天标注是什么意思？

ML 训练需要 **30 个已标注日历日**（日出窗口 03:00–07:00 各标一次），含正负样本。

建议：有云海 10–15 天 + 无云海 15–20 天。

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

# 部署：更新 CLOUDSEA_MODEL_PATH 并重启容器
```

详见 [`internal/open-annotation-plan.md`](open-annotation-plan.md)
