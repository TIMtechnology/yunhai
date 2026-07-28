#!/usr/bin/env bash
# 标准生产发版：门禁 → 构建镜像 → 备份/上传 → compose 重启 → 冒烟测试
# 用法：bash scripts/release-prod.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "错误: 生产发布只能从 main 分支执行" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "错误: 工作区不干净，拒绝生产发布" >&2
  exit 1
fi

BUILD_ID="$(git rev-parse --short=12 HEAD)"
TAR="yunhai-${BUILD_ID}-amd64.tar"

echo "== 0. 发布门禁 =="
bash scripts/validate-release.sh

echo "== 1. 构建 amd64 镜像 ${BUILD_ID} =="
BUILD_ID="$BUILD_ID" OUTPUT_TAR="$TAR" bash scripts/build-amd64.sh

echo "== 2. 备份并部署到生产 =="
bash scripts/deploy-prod.sh "$TAR"

echo "== 3. 生产冒烟 =="
bash scripts/smoke-prod.sh

if [[ "${INSTALL_CRON:-1}" == "1" ]]; then
  echo "== 4. 安装生产定时任务（不修改 compose / env）==="
  bash scripts/install-prod-cron.sh
fi

echo ""
echo "标准发版完成。"
