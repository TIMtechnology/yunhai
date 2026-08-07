# AI 出行解读（大模型辅助）

## 功能

主页预测侧栏：根据**当日逐时气象**（03–07 日出窗口）、**前一日降水**、**规则 + ML 分数**和**点位专属地理上下文**，调用 OpenAI 兼容 API 生成中文出行建议。

**不替代**数值预测；页面上标注「AI 辅助说明，非预报承诺」。

`verdict_hint` 以 **`ui_display_scores`**（日出时刻，与主页卡片一致）及 03–07 时窗口云海为准；**不用** `day_summary.peak_cloudsea_prob`（全天 24h 最大，午后常虚高）。它只是平台结论参考，不再要求大模型机械保持一致。大模型必须说明自己基于地形、水体和气象的独立判断是否与平台一致。能见度字段为米（`vis_m`）。

上下文另含：

- `prediction_pipeline`：规则%/ML%/融合%、是否 `ml_active`；
- `meteo_analysis_hints`：气象机理线索；
- `site_geography`：服务端可信的点位名称、GCJ-02 地图坐标、WGS84 气象坐标、海拔、区域、近库/湖泊、水库、峰水高差、地形起伏，以及空间谷地气象是否可用。

未接入 ML 的点位禁止编造模型指标；没有可信水体资料时不得自行虚构湖泊或水库。解读须含「点位地形与水体独立研判」「气象机理简析」与「规则引擎与 ML 对照（佐证/驳斥）」等内容，且不得只复述平台分数。

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
