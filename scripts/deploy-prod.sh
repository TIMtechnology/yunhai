#!/usr/bin/env bash
# 生产发布：只更新镜像并重启，绝不覆盖服务器 docker-compose.prod.yml
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=lib/load-deploy-env.sh
source "$(dirname "$0")/lib/load-deploy-env.sh"

REMOTE_DIR="${YUNHAI_REMOTE_DIR:-/opt/yunhai/patch}"
COMPOSE_DIR="${YUNHAI_COMPOSE_DIR:-/opt/yunhai}"
TAR="${1:-yunhai-amd64.tar}"

if [[ ! -f "$TAR" ]]; then
  echo "错误: 镜像包不存在: $TAR（请先运行 ./scripts/build-amd64.sh）" >&2
  exit 1
fi

SCP=(scp -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
SSH=(ssh -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
HOST="$YUNHAI_HOST"

echo "上传 $TAR → $HOST:$REMOTE_DIR/"
"${SCP[@]}" "$TAR" "$HOST:$REMOTE_DIR/"

echo "加载镜像并重启…"
echo "  · 不上传、不覆盖 docker-compose.prod.yml / .env"
echo "  · 仅 docker load + compose up -d yunhai（environment 段保持服务器原样）"
"${SSH[@]}" "$HOST" "set -e
cd $COMPOSE_DIR
if [[ ! -f docker-compose.prod.yml ]]; then
  echo '错误: $COMPOSE_DIR/docker-compose.prod.yml 不存在' >&2
  exit 1
fi
docker load -i $REMOTE_DIR/$(basename "$TAR")
docker compose -f docker-compose.prod.yml up -d --force-recreate yunhai
sleep 10
curl -sf http://127.0.0.1:8088/health
echo ''
docker exec yunhai-yunhai-1 cat /app/BUILD_ID 2>/dev/null || true
echo ''
docker compose -f docker-compose.prod.yml ps
"

echo "完成。未修改服务器 compose / env 文件。"
