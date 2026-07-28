#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# 从 frontend/.env.local 读取高德 Key（不提交 Git）
VITE_AMAP_KEY=""
VITE_AMAP_SECURITY=""
if [[ -f frontend/.env.local ]]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' frontend/.env.local | grep VITE_AMAP | xargs)
  VITE_AMAP_KEY="${VITE_AMAP_KEY:-}"
  VITE_AMAP_SECURITY="${VITE_AMAP_SECURITY:-}"
fi

if [[ -z "${VITE_AMAP_KEY}" ]] || [[ -z "${VITE_AMAP_SECURITY}" ]]; then
  echo "错误: 请在 frontend/.env.local 中配置 VITE_AMAP_KEY 与 VITE_AMAP_SECURITY" >&2
  exit 1
fi

BUILD_ID="${BUILD_ID:-$(git rev-parse --short=12 HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
IMAGE_TAG="yunhai:${BUILD_ID}"
OUTPUT_TAR="${OUTPUT_TAR:-yunhai-${BUILD_ID}-amd64.tar}"

docker build --platform linux/amd64 \
  --build-arg NODE_IMAGE=docker.1ms.run/library/node:20-alpine \
  --build-arg PYTHON_IMAGE=docker.1ms.run/library/python:3.12-slim \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg VITE_AMAP_KEY="${VITE_AMAP_KEY}" \
  --build-arg VITE_AMAP_SECURITY="${VITE_AMAP_SECURITY}" \
  --build-arg BUILD_ID="${BUILD_ID}" \
  -t "${IMAGE_TAG}" \
  -t yunhai:latest .
docker save "${IMAGE_TAG}" yunhai:latest -o "${OUTPUT_TAR}"
echo "镜像已保存: ${OUTPUT_TAR} ($(du -h "${OUTPUT_TAR}" | cut -f1))"
echo "不可变镜像标签: ${IMAGE_TAG}"
echo "标准发版: bash scripts/release-prod.sh"
echo "或分步: bash scripts/deploy-prod.sh ${OUTPUT_TAR} && bash scripts/smoke-prod.sh"
