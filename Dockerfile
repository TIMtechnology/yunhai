# 生产单镜像：前端静态资源 + FastAPI
# 构建（x86）：docker build --platform linux/amd64 -t yunhai:latest .
#
# 基础镜像使用国内镜像代理（docker.1ms.run）；海外环境可改回 node:20-alpine / python:3.12-slim
ARG NODE_IMAGE=docker.1ms.run/library/node:20-alpine
ARG PYTHON_IMAGE=docker.1ms.run/library/python:3.12-slim
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

FROM ${NODE_IMAGE} AS frontend-build
ARG NPM_REGISTRY
ARG VITE_TIANDITU_KEY

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --registry="${NPM_REGISTRY}"
COPY frontend/ ./
ENV VITE_TIANDITU_KEY=$VITE_TIANDITU_KEY
RUN npm run build

FROM ${PYTHON_IMAGE}
ARG PIP_INDEX

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i "${PIP_INDEX}"

COPY backend/app ./app
COPY scripts ./scripts
COPY data/scenic-spots ./data/scenic-spots
COPY data/terrain ./data/terrain
# 模型放在卷挂载目录外，避免 cloudsea_data 覆盖 /app/data/cloudsea
COPY data/cloudsea/models/*.pkl ./models/
COPY --from=frontend-build /app/dist ./static

ENV SCENIC_SPOTS_DIR=/app/data/scenic-spots
ENV TERRAIN_SNAPSHOTS_DIR=/app/data/terrain
ENV STATIC_DIR=/app/static
ENV TZ=Asia/Shanghai

EXPOSE 8080

# 多 worker：CPU 型回测/ML 会占满单核；2 worker 可并行处理请求（按 CPU 核数调整）
ENV UVICORN_WORKERS=2
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS}"]
