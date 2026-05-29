# 云海标注工具

## 30 天标注是什么意思？

ML 训练需要的是 **30 个已标注的日历日**（每个日出窗口 03:00–07:00 标一次），**不是** 30 天「都有云海」。

建议比例：

- **有云海**（完整 / 部分）：约 10–15 天
- **无云海**：约 15–20 天

正负样本都要够，模型才能学会区分。你目前已有 7 天金标准（4 有 + 3 无），再补 **23 天**左右即可启动 ML。

---

## 线上使用（已部署后）

1. 打开：**https://yunhai.timkj.com/label.html**
2. 在页面顶部填入 **Admin Token**（与服务器 `CLOUDSEA_ADMIN_TOKEN` 相同，与 analytics 看板共用）
3. 点击「保存 Token」
4. 选择景区 / 观景点 / 日期，点 **无云海 / 部分 / 完整** 保存

> 标注页为内部工具，不在主导航展示；请收藏上述链接。

---

## 本地开发

```bash
# 终端 1：后端
cd backend && CLOUDSEA_ENABLED=true CLOUDSEA_ADMIN_TOKEN=dev-token uvicorn app.main:app --reload

# 终端 2：标注页
cd frontend && npm run dev
# 打开 http://127.0.0.1:5173/label.html
```

---

## 快捷键

- `1` / `2` / `3`：无云海 / 部分 / 完整
- `←` / `→`：上一天 / 下一天

---

## 预置金标准（五女山·点将台，库内已 seed）

| 日期 | 标注 |
|------|------|
| 5/4, 5/9, 5/22, 5/29 | 有云海（完整） |
| 5/24, 5/25, 5/28 | 无云海 |

---

## 回测 API（可选）

```bash
curl -H "X-Cloudsea-Token: <你的token>" \
  "https://yunhai.timkj.com/api/internal/backtest/predict?date=2026-05-29&spot_id=wunvshan&viewpoint_id=dianjiangtai"
```

---

## 标注完成后

在标注页底部可查看「回测准确率」。累计 **≥30 个标注日** 后联系开发 ML 训练（Logistic 可解释版 → 替换 03–07 点云海概率）。
