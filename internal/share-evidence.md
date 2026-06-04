# 分享预测与依据快照

## 功能

主页右侧预测面板提供两个入口：

- `分享本日预测`：生成公开分享页 `/s/{id}`，复制链接并打开页面。
- `生成分享图`：生成同一快照后直接打开 `/api/share/{id}/image.png`，用户可保存或复制图片转发。

分享内容是不可变快照，默认 Redis TTL 为 `SHARE_SNAPSHOT_TTL=604800`（7 天）。

## 依据口径

分享依据复用 `backend/app/services/evidence_builder.py`，与 AI 出行解读同源：

- `verdict_hint`：与主页卡片一致，使用日出时刻/日出窗口云海概率。
- `prediction_pipeline`：规则、ML、融合分拆解；`ml_active=false` 时禁止展示 ML/LOOCV。
- `hourly_evidence_table`：03:00-07:00 逐时气象表。
- `meteo_analysis_hints`：湿度、低云、能见度、前日降水与观云模式的机理线索。

## OG / 图片

OG 与分享图由 `backend/app/services/share_og_renderer.py` 使用 Pillow 本地合成。

背景图优先读取 `SHARE_ASSETS_DIR` 下的 `bg_*.png`。当前背景资源：

- `data/share-assets/bg_cloudsea_strong.png`
- 来源：APIMart `gpt-image-2`
- 任务：`task_01KT6S2SWD903W8J81RDAQ31PJ`
- 尺寸：2048x1152

若没有背景图，会退回程序化渐变山形背景。

## 运行配置

```bash
PUBLIC_BASE_URL=https://yunhai.timqian.com
SHARE_SNAPSHOT_TTL=604800
SHARE_ASSETS_DIR=/app/data/share-assets
SHARE_CACHE_DIR=/app/data/share-cache
APIMART_ENABLED=false
APIMART_API_KEY=
SHARE_OG_USE_IMAGE2=true
```

`APIMART_API_KEY` 仅用于离线生成背景模板；不要提交到 Git，也不要写入文档。

## 离线生成背景

```bash
python3 scripts/generate_share_assets.py --only cloudsea_strong
```

若未设置 `APIMART_API_KEY`，脚本会用 `getpass` 交互输入，不回显、不写文件。
