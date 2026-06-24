# 生产单镜像：前端静态 + FastAPI + ML 模型 + 社区精选落库种子
# 构建：./scripts/build-amd64.sh
# 发版：./scripts/release-prod.sh
#
ARG NODE_IMAGE=docker.1ms.run/library/node:20-alpine
ARG PYTHON_IMAGE=docker.1ms.run/library/python:3.12-slim
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG BUILD_ID=dev

FROM ${NODE_IMAGE} AS frontend-build
ARG NPM_REGISTRY
ARG VITE_AMAP_KEY
ARG VITE_AMAP_SECURITY

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --registry="${NPM_REGISTRY}"
COPY frontend/ ./
ENV VITE_AMAP_KEY=$VITE_AMAP_KEY
ENV VITE_AMAP_SECURITY=$VITE_AMAP_SECURITY
RUN npm run build

FROM ${PYTHON_IMAGE}
ARG PIP_INDEX
ARG BUILD_ID

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i "${PIP_INDEX}"

COPY backend/app ./app
COPY scripts/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

COPY data/scenic-spots ./data/scenic-spots
COPY data/terrain ./data/terrain
COPY data/cloudsea/models/spot_*.pkl ./baked/models/
COPY data/cloudsea/curated-spots ./baked/curated-spots
COPY --from=frontend-build /app/dist ./static

RUN printf '%s\n' "${BUILD_ID}" > /app/BUILD_ID

ENV SCENIC_SPOTS_DIR=/app/data/scenic-spots
ENV TERRAIN_SNAPSHOTS_DIR=/app/data/terrain
ENV STATIC_DIR=/app/static
ENV TZ=Asia/Shanghai
ENV UVICORN_WORKERS=2

EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]
