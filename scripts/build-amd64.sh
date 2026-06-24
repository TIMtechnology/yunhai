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

docker build --platform linux/amd64 \
  --build-arg NODE_IMAGE=docker.1ms.run/library/node:20-alpine \
  --build-arg PYTHON_IMAGE=docker.1ms.run/library/python:3.12-slim \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg VITE_AMAP_KEY="${VITE_AMAP_KEY}" \
  --build-arg VITE_AMAP_SECURITY="${VITE_AMAP_SECURITY}" \
  --build-arg BUILD_ID="${BUILD_ID:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}" \
  -t yunhai:latest .
docker save yunhai:latest -o yunhai-amd64.tar
echo "镜像已保存: yunhai-amd64.tar ($(du -h yunhai-amd64.tar | cut -f1))"
echo "标准发版: bash scripts/release-prod.sh"
echo "或分步: bash scripts/deploy-prod.sh yunhai-amd64.tar && bash scripts/smoke-prod.sh"
