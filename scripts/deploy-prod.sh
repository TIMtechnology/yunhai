#!/usr/bin/env bash
# 生产发布：只更新镜像并重启，绝不覆盖服务器 docker-compose.prod.yml
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=lib/load-deploy-env.sh
source "$(dirname "$0")/lib/load-deploy-env.sh"

REMOTE_DIR="${YUNHAI_REMOTE_DIR:-/opt/yunhai}"
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

echo "加载镜像并重启（保留 compose.prod.yml 与 env）…"
"${SSH[@]}" "$HOST" "set -e
cd $REMOTE_DIR
if [[ ! -f docker-compose.prod.yml ]]; then
  echo '错误: docker-compose.prod.yml 不存在，请先在服务器手工创建生产 compose' >&2
  exit 1
fi
cp -a docker-compose.prod.yml docker-compose.prod.yml.bak-\$(date +%Y%m%d%H%M%S)
docker load -i $(basename "$TAR")
docker compose -f docker-compose.prod.yml up -d --force-recreate yunhai
sleep 5
curl -sf http://127.0.0.1:8088/health
echo ''
docker compose -f docker-compose.prod.yml ps
"

echo "完成。生产 env 未改动；备份见服务器 ${REMOTE_DIR}/docker-compose.prod.yml.bak-*"
