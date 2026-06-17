#!/usr/bin/env bash
# 生产热补丁：仅 docker cp 进运行中容器 + restart，不碰 compose / env
set -euo pipefail
cd "$(dirname "$0")/.."

SSH_KEY="${YUNHAI_SSH_KEY:-/Users/likun/ssh_2025}"
HOST="${YUNHAI_HOST:-root@182.203.168.140}"
REMOTE_DIR="${YUNHAI_REMOTE_DIR:-/opt/yunhai/patch}"
CONTAINER="${YUNHAI_CONTAINER:-yunhai-yunhai-1}"

SSH=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
SCP=(scp -i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

echo "== 1. 本地构建前端（Key 来自 frontend/.env.local，不进服务器 env）=="
(cd frontend && npm run build)

echo "== 2. 打包补丁（不含任何 .env / compose）=="
tar czf /tmp/yunhai-static.tar.gz -C frontend/dist .
tar czf /tmp/yunhai-spots.tar.gz -C data scenic-spots
tar czf /tmp/yunhai-backend-patch.tar.gz \
  backend/app/engine/coord_transform.py \
  backend/app/models/schemas.py \
  backend/app/services/predictor.py \
  backend/app/routers/api.py

echo "== 3. 上传到 ${HOST}:${REMOTE_DIR} [不覆盖 compose/env] =="
"${SSH[@]}" "$HOST" "mkdir -p $REMOTE_DIR"
"${SCP[@]}" /tmp/yunhai-static.tar.gz /tmp/yunhai-spots.tar.gz /tmp/yunhai-backend-patch.tar.gz "$HOST:$REMOTE_DIR/"

echo "== 4. 应用热补丁（仅 docker cp + restart）=="
"${SSH[@]}" "$HOST" "set -e
cd $REMOTE_DIR
tar xzf yunhai-backend-patch.tar.gz
docker cp backend/app/engine/coord_transform.py $CONTAINER:/app/app/engine/coord_transform.py
docker cp backend/app/models/schemas.py $CONTAINER:/app/app/models/schemas.py
docker cp backend/app/services/predictor.py $CONTAINER:/app/app/services/predictor.py
docker cp backend/app/routers/api.py $CONTAINER:/app/app/routers/api.py
tar xzf yunhai-spots.tar.gz
docker cp scenic-spots/. $CONTAINER:/app/data/scenic-spots/
rm -rf static-new && mkdir -p static-new && tar xzf yunhai-static.tar.gz -C static-new
docker cp static-new/. $CONTAINER:/app/static/
docker restart $CONTAINER
sleep 6
curl -sf http://127.0.0.1:8088/health
echo ''
curl -sf http://127.0.0.1:8088/api/spots/wunvshan | python3 -c \"import json,sys; d=json.load(sys.stdin); print('coord_sys:', d.get('coord_sys'), 'vp:', d['viewpoints'][0]['lat'], d['viewpoints'][0]['lng'])\"
"

echo ""
echo "完成。未修改服务器 docker-compose.prod.yml 及任何 env 文件。"
