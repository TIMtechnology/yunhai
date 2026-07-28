#!/usr/bin/env bash
# 生产发布：只更新镜像并重启，绝不覆盖服务器 docker-compose.prod.yml
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=lib/load-deploy-env.sh
source "$(dirname "$0")/lib/load-deploy-env.sh"

REMOTE_DIR="${YUNHAI_REMOTE_DIR:-/opt/yunhai/patch}"
COMPOSE_DIR="${YUNHAI_COMPOSE_DIR:-/opt/yunhai}"
TAR="${1:-}"

if [[ -z "$TAR" || ! -f "$TAR" ]]; then
  echo "错误: 镜像包不存在: $TAR（请先运行 ./scripts/build-amd64.sh）" >&2
  exit 1
fi
TAR_NAME="$(basename "$TAR")"
if [[ ! "$TAR_NAME" =~ ^yunhai-[0-9a-f]{12}-amd64\.tar$ ]]; then
  echo "错误: 镜像包名必须包含 12 位 Git 提交号: $TAR_NAME" >&2
  exit 1
fi

SCP=(scp -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
SSH=(ssh -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
HOST="$YUNHAI_HOST"

echo "上传 $TAR → $HOST:$REMOTE_DIR/"
"${SCP[@]}" "$TAR" "$HOST:$REMOTE_DIR/$TAR_NAME"

echo "备份、加载镜像并重启…"
echo "  · 不上传、不覆盖 docker-compose.prod.yml / .env"
echo "  · 仅 docker load + compose up -d yunhai（environment 段保持服务器原样）"
"${SSH[@]}" "$HOST" "set -euo pipefail
cd '$COMPOSE_DIR'
if [[ ! -f docker-compose.prod.yml ]]; then
  echo '错误: docker-compose.prod.yml 不存在' >&2
  exit 1
fi

STAMP=\$(date +%Y%m%d-%H%M%S)
BACKUP_DIR='$COMPOSE_DIR/backups/'\"\$STAMP\"
mkdir -p \"\$BACKUP_DIR\"
chmod 700 '$COMPOSE_DIR/backups' \"\$BACKUP_DIR\"
cp -a docker-compose.prod.yml \"\$BACKUP_DIR/\"
for secret_file in .env .env.security; do
  if [[ -f \"\$secret_file\" ]]; then
    cp -a \"\$secret_file\" \"\$BACKUP_DIR/\"
  fi
done

CONTAINER='${YUNHAI_CONTAINER:-yunhai-yunhai-1}'
PREVIOUS_IMAGE=\$(docker image inspect yunhai:latest --format '{{.Id}}' 2>/dev/null || true)
if [[ -n \"\$PREVIOUS_IMAGE\" ]]; then
  printf '%s\n' \"\$PREVIOUS_IMAGE\" > \"\$BACKUP_DIR/previous-image-id\"
  docker save \"\$PREVIOUS_IMAGE\" -o \"\$BACKUP_DIR/previous-image.tar\"
fi
if docker inspect \"\$CONTAINER\" >/dev/null 2>&1; then
  for path in /app/data/analytics/analytics.db /app/data/cloudsea/cloudsea.db; do
    docker cp \"\$CONTAINER:\$path\" \"\$BACKUP_DIR/\" 2>/dev/null || true
  done
fi

rollback() {
  trap - ERR
  echo '部署失败，开始自动回滚' >&2
  if [[ -n \"\$PREVIOUS_IMAGE\" ]]; then
    docker tag \"\$PREVIOUS_IMAGE\" yunhai:latest
    docker compose -f docker-compose.prod.yml up -d --force-recreate yunhai || true
  fi
}
trap rollback ERR

docker load -i '$REMOTE_DIR/$TAR_NAME'
docker compose -f docker-compose.prod.yml up -d --force-recreate yunhai
healthy=0
for _ in {1..30}; do
  if curl -sf http://127.0.0.1:8088/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
if [[ \"\$healthy\" != 1 ]]; then
  echo '健康检查超时' >&2
  rollback
  exit 1
fi
trap - ERR

curl -sf http://127.0.0.1:8088/health
echo ''
docker exec \"\$CONTAINER\" cat /app/BUILD_ID 2>/dev/null || true
echo ''
docker compose -f docker-compose.prod.yml ps
echo \"备份目录: \$BACKUP_DIR\"
"

echo "完成。未修改服务器 compose / env 文件。"
