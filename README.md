<div align="center">

# 🌅 日出云海 · 联合观测预测

**未来 5 天逐小时云海 / 日出观测概率 · 模糊逻辑 + 标注驱动 ML · 天地图选点 · Himawari 卫星**

<br />

[![在线体验](https://img.shields.io/badge/🌐_在线体验-yunhai.timkj.com-059669?style=for-the-badge&logo=googlechrome&logoColor=white)](https://yunhai.timkj.com/)
[![使用指南](https://img.shields.io/badge/📖_使用指南-文档-0284C7?style=for-the-badge)](https://yunhai.timkj.com/docs/index.html)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)

<br />

[立即体验 →](https://yunhai.timkj.com/) · [预测模型](https://yunhai.timkj.com/docs/prediction-model.html) · [系统架构](https://yunhai.timkj.com/docs/architecture.html)

<br />

![日出云海预测仪表盘](docs/images/dashboard.jpg)

*本溪五女山 · 点将台 — 5 天时间轴、双场景概率、分层因子拆解与雷达图*

</div>

---

## ✨ 项目简介

面向摄影爱好者、景区运营与气象景观研究者的 **免费 Web 预测系统**：在地图上选择观景点（或搜索 POI），查看未来 **5 天 × 120 小时** 的云海、日出观测参考概率，并结合 **Himawari 卫星红外云图** 做区域云量辅助判断。

> 🌍 **在线地址：[https://yunhai.timkj.com/](https://yunhai.timkj.com/)**

系统采用 **「规则引擎 + 机器学习 + 底层观测」三层因子架构**：

| 层级 | 说明 |
|------|------|
| **规则引擎** | 基于文献的模糊逻辑评分（850/700 hPa 垂直场、逆温、低云/能见度补偿、Type A/B 型态） |
| **ML 模型** | 日出窗口 03–07 点由标注数据训练的 Logistic 回归接管概率（v2，22 维日特征） |
| **底层观测** | 分层云量、垂直场剖面、云海型态、地面态等原始气象字段完整展示 |

内置 **五女山、大黑山、黄山、庐山** 等精选景区观景点，也支持天地图 POI 搜索与地图拖拽自定义坐标。配套 **标注 / 回测 / ML 训练** 闭环（见 [`internal/CLOUDSEA-LABEL.md`](internal/CLOUDSEA-LABEL.md)）。

> ⚠️ 预测结果为**参考概率**，基于 Open-Meteo 免费预报与本地模型，**不替代**官方气象预警与现场判断。

---

## 🖼️ 功能亮点

| 功能 | 说明 |
|------|------|
| 🗺️ **地图选点** | 天地图底图，POI 搜索、标记拖拽 / 点击微调坐标 |
| 📊 **双场景预测** | 云海 + 日出概率，综合场景评分与五级适宜等级 |
| 🧠 **混合预测引擎** | 03–07 点 ML 概率 + 完整规则因子 + 底层观测数据 |
| ⏱️ **5 天时间轴** | 逐小时折线图，标注日出时刻与推荐观测窗口 |
| 📡 **因子拆解** | 雷达图（加权评分维度）+ 列表（含模型层 / 观测层） |
| 🛰️ **卫星云图** | Himawari 红外裁切，当天已发生时段轻量校正 |
| 🏔️ **精选景区** | 预置观景点海拔、坐标、季节权重；支持全国 POI |
| 🔬 **标注回测** | 内部标注页、Historical Forecast 回测、ML 重训脚本 |

---

## 🏔️ 内置景区

| 景区 | 说明 |
|------|------|
| 本溪五女山 | 点将台、观云亭（金标准标注与 ML 主标定区） |
| 大连大黑山 | 观日峰等 |
| 黄山 / 庐山 / 泰山 / 峨眉山 / 华山 / 五台山 | 经典观云观日点位 |

数据文件：[`data/scenic-spots/`](data/scenic-spots/)，可自行扩展 JSON。

---

## 🛠️ 技术栈

```
┌──────────────────────────────────────────────────────────────────┐
│  Vue 3 · Vite · TypeScript · Tailwind · Naive UI · ECharts      │
│  天地图 JS API 4.0 · Pinia · axios · suncalc                     │
├──────────────────────────────────────────────────────────────────┤
│  FastAPI · Uvicorn · httpx · Pydantic · scikit-learn · Redis    │
│  Open-Meteo Forecast / Historical · GIBS Himawari · SQLite       │
│  模糊逻辑 v2（Archetype）· ML v2 · 标注样本库 · 行为分析          │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 环境要求

- Node.js 20+ · Python 3.12+ · （可选）Redis 7
- [天地图开发者 Key](https://console.tianditu.gov.cn/)（浏览器端类型）

### 1. 克隆与配置

```bash
git clone https://github.com/TIMtechnology/yunhai.git
cd yunhai
cp frontend/.env.example frontend/.env.local
# 编辑 frontend/.env.local，填入 VITE_TIANDITU_KEY
```

### 2. 本地开发

```bash
# 终端 1 — 后端
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 终端 2 — 前端
cd frontend && npm install && npm run dev
# http://localhost:5173
```

### 3. 启用云海 ML（可选）

```bash
export CLOUDSEA_ML_ENABLED=true
export CLOUDSEA_MODEL_PATH=../data/cloudsea/models/cloudsea_ml_v2.pkl
```

### 4. Docker

```bash
docker compose up --build          # 开发
bash scripts/build-amd64.sh        # 生产 amd64 镜像
docker compose -f docker-compose.prod.yml up -d
```

生产部署见 [`docker-compose.deploy.yml`](docker-compose.deploy.yml)。

---

## 📡 API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/spots/search?q=` | 搜索景区 |
| `GET` | `/api/predict/{spot_id}/viewpoint/{viewpoint_id}` | 预置观景点预测 |
| `POST` | `/api/predict` | 自定义坐标预测 |
| `GET` | `/api/satellite/cloud` | Himawari 红外裁切 |
| `GET` | `/api/internal/backtest/predict` | 历史日回测（需 Token） |
| `GET` | `/api/internal/cloudsea/label-session` | 标注会话（需 Token） |

Swagger：`http://localhost:8000/docs`

---

## 🔬 ML 与标注工作流

```bash
# 1. 回填标注日完整垂直场气象
python3 scripts/backfill_meteo_hourly.py

# 2. 训练 ML v2（32+ 标注日）
python3 scripts/train_cloudsea_model.py

# 3. 模型输出
# data/cloudsea/models/cloudsea_ml_v2.pkl
```

标注页（内部）：`https://yunhai.timkj.com/label.html` · 详见 [`internal/CLOUDSEA-LABEL.md`](internal/CLOUDSEA-LABEL.md)

---

## 📁 项目结构

```
yunhai/
├── frontend/                 # Vue 3 主应用 + label.html 标注页
├── backend/app/
│   ├── adapters/             # Open-Meteo、Historical、Himawari WMS
│   ├── engine/               # cloudsea_scorer · cloudsea_ml · cloudsea_features
│   ├── routers/              # api · cloudsea · analytics
│   └── services/             # predictor · cloudsea_store · cache
├── data/
│   ├── scenic-spots/         # 景区 JSON
│   └── cloudsea/             # 标注库 cloudsea.db · ML 模型
├── docs/docs/                # 用户文档（同步至 frontend/public/docs）
├── internal/                 # 标注说明 · 分析看板（本地内部）
├── scripts/                  # build-amd64 · train · backfill
└── Dockerfile
```

---

## 📖 文档

| 文档 | 链接 |
|------|------|
| 使用指南 | [/docs/index.html](https://yunhai.timkj.com/docs/index.html) |
| 数据来源 | [/docs/data-sources.html](https://yunhai.timkj.com/docs/data-sources.html) |
| 预测模型 | [/docs/prediction-model.html](https://yunhai.timkj.com/docs/prediction-model.html) |
| 系统架构 | [/docs/architecture.html](https://yunhai.timkj.com/docs/architecture.html) |
| 界面设计 | [/docs/ui-design.html](https://yunhai.timkj.com/docs/ui-design.html) |

源码副本：`frontend/public/docs/`

---

## 🌐 部署

官方实例：**[yunhai.timkj.com](https://yunhai.timkj.com/)** · Docker 单镜像 + Redis · 时区 `Asia/Shanghai`

---

## 🤝 贡献

欢迎 Issue / PR：观景点 JSON、标注样本、模型优化、文档完善等。

---

## 📄 开源协议

[MIT License](LICENSE) · © 2026 [timkj](https://yunhai.timkj.com/)

---

<div align="center">

**如果这个项目对你规划观云、拍日出或开展研究有帮助，欢迎 Star ⭐**

[⬆ 回到顶部](#-日出云海--联合观测预测)

</div>
