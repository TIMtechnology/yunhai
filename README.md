<div align="center">

# 🌅 日出云海 · 联合观测预测

**未来 5 天逐小时云海 / 日出观测概率 · 天地图选点 · Himawari 卫星云图**

<br />

[![在线体验](https://img.shields.io/badge/🌐_在线体验-yunhai.timkj.com-059669?style=for-the-badge&logo=googlechrome&logoColor=white)](https://yunhai.timkj.com/)
[![使用指南](https://img.shields.io/badge/📖_使用指南-文档-0284C7?style=for-the-badge)](https://yunhai.timkj.com/docs/index.html)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)

<br />

[立即体验 →](https://yunhai.timkj.com/) · [数据来源说明](https://yunhai.timkj.com/docs/data-sources.html) · [预测模型](https://yunhai.timkj.com/docs/prediction-model.html)

</div>

---

## ✨ 项目简介

面向摄影爱好者与户外玩家的 **免费 Web 工具**：在地图上选择观景点（或搜索 POI），查看未来 **5 天 × 24 小时** 的云海、日出观测参考概率，并结合 **Himawari 卫星红外云图** 做区域云量辅助判断。

> 🌍 **在线地址：[https://yunhai.timkj.com/](https://yunhai.timkj.com/)**

内置 **五女山、大黑山、黄山、庐山** 等精选景区观景点，也支持天地图 POI 搜索与地图拖拽自定义坐标。

> ⚠️ 预测结果为**参考概率**，基于 Open-Meteo 免费预报与简化模糊逻辑模型，**不替代**官方气象预警与现场判断。

---

## 🖼️ 功能亮点

| 功能 | 说明 |
|------|------|
| 🗺️ **地图选点** | 天地图底图，支持 POI 搜索、标记拖拽 / 点击微调坐标 |
| 📊 **双场景预测** | 云海概率 + 日出概率，综合评分与等级（极佳 / 良好 / 一般…） |
| ⏱️ **5 天时间轴** | 逐小时折线图，自动标注日出时刻与推荐观测窗口 |
| 🛰️ **卫星云图** | Himawari 红外裁切，辅助判断区域低云与中高云 |
| 🏔️ **精选景区** | 预置观景点海拔、坐标；支持全国任意 POI |
| 📱 **PC 优先** | 深色仪表盘布局，ECharts 雷达图展示场景因子 |

---

## 🏔️ 内置景区

| 景区 | 说明 |
|------|------|
| 本溪五女山 | 点将台、观云亭等（优先调试） |
| 大连大黑山 | 观日峰等 |
| 黄山 / 庐山 / 泰山 / 峨眉山 / 华山 / 五台山 | 经典观云观日点位 |

数据文件位于 [`data/scenic-spots/`](data/scenic-spots/)，可自行扩展 JSON。

---

## 🛠️ 技术栈

```
┌─────────────────────────────────────────────────────────┐
│  Vue 3 · Vite · TypeScript · Tailwind · Naive UI · ECharts │
│  天地图 JS API 4.0 · Pinia · axios · suncalc              │
├─────────────────────────────────────────────────────────┤
│  FastAPI · Uvicorn · httpx · Pydantic · Redis（可选缓存）   │
│  Open-Meteo · GIBS Himawari · 模糊逻辑评分引擎              │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 环境要求

- Node.js 20+
- Python 3.12+
- （可选）Redis 7
- [天地图开发者 Key](https://console.tianditu.gov.cn/)（**浏览器端**类型）

### 1. 克隆仓库

```bash
git clone https://github.com/TIMtechnology/yunhai.git
cd yunhai
```

### 2. 配置天地图 Key

```bash
cp frontend/.env.example frontend/.env.local
# 编辑 frontend/.env.local，填入 VITE_TIANDITU_KEY
```

### 3. 本地开发

```bash
# 终端 1 — 后端
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 终端 2 — 前端
cd frontend
npm install
npm run dev
```

浏览器打开 **http://localhost:5173**

### 4. Docker 开发模式

```bash
docker compose up --build
```

### 5. 生产镜像（单容器：静态前端 + API）

```bash
# 需先在 frontend/.env.local 配置 VITE_TIANDITU_KEY
bash scripts/build-amd64.sh
docker compose -f docker-compose.prod.yml up -d
# 默认映射 http://localhost:8088
```

服务器仅加载预构建镜像时，使用 [`docker-compose.deploy.yml`](docker-compose.deploy.yml)。

---

## 📡 API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/spots/search?q=` | 搜索景区 |
| `GET` | `/api/spots/{spot_id}` | 景区详情 |
| `GET` | `/api/spots/{spot_id}/viewpoints/{viewpoint_id}` | 观景点详情 |
| `POST` | `/api/predict` | 自定义坐标预测 |
| `GET` | `/api/predict/{spot_id}/viewpoint/{viewpoint_id}` | 预置观景点预测 |
| `GET` | `/api/weather/raw` | 原始 Open-Meteo 数据（调试） |
| `GET` | `/api/satellite/cloud` | Himawari 红外云图裁切 |

完整交互式文档：启动后端后访问 `/docs`（Swagger UI）。

---

## 📁 项目结构

```
yunhai/
├── frontend/          # Vue 3 前端
├── backend/app/       # FastAPI 应用
│   ├── adapters/      # Open-Meteo、卫星 WMS 适配
│   ├── engine/        # 云海 / 日出评分引擎
│   └── services/      # 预测编排、缓存
├── data/scenic-spots/ # 景区 JSON 数据
├── scripts/           # amd64 镜像构建脚本
├── Dockerfile         # 生产单镜像
└── docker-compose*.yml
```

---

## 📖 文档

| 文档 | 链接 |
|------|------|
| 使用指南 | [/docs/index.html](https://yunhai.timkj.com/docs/index.html) |
| 数据来源 | [/docs/data-sources.html](https://yunhai.timkj.com/docs/data-sources.html) |
| 预测模型 | [/docs/prediction-model.html](https://yunhai.timkj.com/docs/prediction-model.html) |
| 系统架构 | [/docs/architecture.html](https://yunhai.timkj.com/docs/architecture.html) |

源码副本：`frontend/public/docs/`

---

## 🌐 部署与域名

官方实例：**[yunhai.timkj.com](https://yunhai.timkj.com/)**

生产环境采用 Docker 单镜像 + 内网 Redis，时区固定为 `Asia/Shanghai`，确保 Open-Meteo 逐小时数据与日出时刻对齐。

---

## 🤝 贡献

欢迎 Issue / PR：新增观景点 JSON、优化评分模型、改进 UI 等。

---

## 📄 开源协议

[MIT License](LICENSE) · © 2026 [timkj](https://yunhai.timkj.com/)

---

<div align="center">

**如果这个项目对你规划观云、拍日出有帮助，欢迎 Star ⭐**

[⬆ 回到顶部](#-日出云海--联合观测预测)

</div>
