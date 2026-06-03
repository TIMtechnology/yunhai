# AI 出行解读（大模型辅助）

## 功能

主页预测侧栏：根据**当日逐时气象**（03–07 日出窗口）、**前一日降水**、**规则 + ML 分数**、**点位 ML 训练 LOOCV** 等结构化事实，调用 OpenAI 兼容 API 生成中文出行建议。

**不替代**数值预测；页面上标注「AI 辅助说明，非预报承诺」。

`verdict_hint` 以 **`ui_display_scores`**（日出时刻，与主页卡片一致）及 03–07 时窗口云海为准；**不用** `day_summary.peak_cloudsea_prob`（全天 24h 最大，午后常虚高）。大模型须与 `verdict_hint` 一致。能见度字段为米（`vis_m`）。

上下文另含 `prediction_pipeline`（规则%/ML%/融合%、是否 `ml_active`）、`meteo_analysis_hints`（气象机理线索）。未接入 ML 的点位禁止编造模型指标；解读须含「气象机理简析」与「规则引擎与 ML 对照（佐证/驳斥）」两节。

## 环境变量

| 变量 | 说明 |
|------|------|
| `LLM_ADVISORY_ENABLED` | `true` 开启 |
| `LLM_API_KEY` | API 密钥 |
| `LLM_BASE_URL` | 默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 默认 `deepseek-chat` |
| `LLM_ADVISORY_CACHE_TTL` | 秒，默认 86400（1 天）；键含气象指纹，预报变化自动失效 |

## API

`POST /api/advisory/daily-brief`

```json
{
  "date": "2026-06-01",
  "prediction": { "...": "与 /api/predict 相同结构" },
  "refresh": false
}
```

## 前端

`PredictPanel` → `AiAdvisoryPanel`，切换日期自动请求；可点「刷新解读」传 `refresh: true` 强制重新调用大模型。
