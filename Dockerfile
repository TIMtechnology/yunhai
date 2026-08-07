# 生产单镜像：前端静态 + FastAPI + 社区精选落库种子
# 构建：./scripts/build-amd64.sh
# 发版：./scripts/release-prod.sh
#
ARG NODE_IMAGE=docker.1ms.run/library/node:20-alpine
ARG PYTHON_IMAGE=docker.1ms.run/library/python:3.12-slim
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
FROM ${NODE_IMAGE} AS frontend-build
ARG NPM_REGISTRY

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --registry="${NPM_REGISTRY}"
COPY frontend/ ./
ARG VITE_AMAP_KEY
ARG VITE_AMAP_SECURITY
ENV VITE_AMAP_KEY=$VITE_AMAP_KEY
ENV VITE_AMAP_SECURITY=$VITE_AMAP_SECURITY
RUN npm run build

FROM ${PYTHON_IMAGE}
ARG PIP_INDEX

WORKDIR /app

COPY backend/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock -i "${PIP_INDEX}" \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY backend/app ./app
COPY scripts/docker-entrypoint.sh /app/docker-entrypoint.sh
COPY scripts/backfill_spatial_forecast_archive.py scripts/train_cloudsea_model.py scripts/train_operational_model.py /app/scripts/
RUN groupadd --gid 10001 yunhai \
    && useradd --uid 10001 --gid yunhai --no-create-home --shell /usr/sbin/nologin yunhai \
    && chmod +x /app/docker-entrypoint.sh

COPY data/scenic-spots ./data/scenic-spots
COPY data/terrain ./data/terrain
COPY data/cloudsea/curated-spots ./baked/curated-spots
COPY data/cloudsea/models ./baked/models
COPY --from=frontend-build /app/dist ./static

ARG BUILD_ID=dev
RUN mkdir -p /app/data/analytics /app/data/cloudsea /app/data/share-cache /app/data/share-assets \
    && chown -R yunhai:yunhai /app/data \
    && printf '%s\n' "${BUILD_ID}" > /app/BUILD_ID

ENV SCENIC_SPOTS_DIR=/app/data/scenic-spots
ENV TERRAIN_SNAPSHOTS_DIR=/app/data/terrain
ENV STATIC_DIR=/app/static
ENV ANALYTICS_DB_PATH=/app/data/analytics/analytics.db
ENV CLOUDSEA_DB_PATH=/app/data/cloudsea/cloudsea.db
ENV CLOUDSEA_CURATED_SPOTS_DIR=/app/data/cloudsea/curated-spots
ENV CLOUDSEA_MODEL_PATH=/app/baked/models/spot_wunvshan_dianjiangtai.json
ENV CLOUDSEA_ML_ENABLED=false
ENV CLOUDSEA_ALLOW_LEGACY_PICKLE_MODELS=false
ENV SHARE_ASSETS_DIR=/app/data/share-assets
ENV SHARE_CACHE_DIR=/app/data/share-cache
ENV TZ=Asia/Shanghai
ENV UVICORN_WORKERS=2
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
