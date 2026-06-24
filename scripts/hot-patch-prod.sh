#!/usr/bin/env bash
# 生产热补丁：仅 docker cp 进运行中容器 + restart，不碰 compose / env
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=lib/load-deploy-env.sh
source "$(dirname "$0")/lib/load-deploy-env.sh"

SSH=(ssh -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
SCP=(scp -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
HOST="$YUNHAI_HOST"

echo "== 1. 本地构建前端（Key 来自 frontend/.env.local，不进服务器 env）=="
(cd frontend && npm run build)

echo "== 2. 训练 V7 模型（五女山 + 东灵山，若已训练可跳过）=="
if [[ "${SKIP_TRAIN:-0}" != "1" ]]; then
  python3 scripts/train_cloudsea_model.py \
    --db data/cloudsea/cloudsea.prod.db \
    --spot-id wunvshan --viewpoint-id dianjiangtai \
    --window v7 --mode oracle --enhanced --db-only
  python3 scripts/train_cloudsea_model.py \
    --db data/cloudsea/cloudsea.prod.db \
    --spot-id donglingshan --viewpoint-id fengding \
    --window v7 --mode oracle --enhanced --db-only
fi

echo "== 3. 打包补丁（不含任何 .env / compose）=="
tar czf /tmp/yunhai-static.tar.gz -C frontend/dist .
tar czf /tmp/yunhai-spots.tar.gz -C data scenic-spots
tar czf /tmp/yunhai-curated-spots.tar.gz -C data/cloudsea curated-spots
tar czf /tmp/yunhai-models.tar.gz -C data/cloudsea/models \
  spot_wunvshan_dianjiangtai.pkl \
  spot_donglingshan_fengding.pkl
tar czf /tmp/yunhai-backend-patch.tar.gz \
  backend/app/engine/cloudsea_features.py \
  backend/app/engine/cloudsea_ml.py \
  backend/app/engine/cloudsea_scorer.py \
  backend/app/engine/coord_transform.py \
  backend/app/models/schemas.py \
  backend/app/routers/api.py \
  backend/app/routers/cloudsea.py \
  backend/app/services/cloudsea_store.py \
  backend/app/services/prediction_feedback.py \
  backend/app/services/predictor.py \
  backend/app/services/meteo_backfill.py \
  backend/app/services/spot_loader.py \
  backend/app/services/contribute_rate_limit.py \
  backend/app/services/cache.py \
  backend/app/routers/contribute.py

echo "== 4. 上传到 ${HOST}:${REMOTE_DIR} [不覆盖 compose/env] =="
"${SSH[@]}" "$HOST" "mkdir -p $REMOTE_DIR"
"${SCP[@]}" /tmp/yunhai-static.tar.gz /tmp/yunhai-spots.tar.gz \
  /tmp/yunhai-curated-spots.tar.gz \
  /tmp/yunhai-models.tar.gz /tmp/yunhai-backend-patch.tar.gz \
  "$HOST:$REMOTE_DIR/"

echo "== 5. 应用热补丁（仅 docker cp + restart）=="
"${SSH[@]}" "$HOST" "set -e
cd $REMOTE_DIR
tar xzf yunhai-backend-patch.tar.gz
for f in \
  backend/app/engine/cloudsea_features.py \
  backend/app/engine/cloudsea_ml.py \
  backend/app/engine/cloudsea_scorer.py \
  backend/app/engine/coord_transform.py \
  backend/app/models/schemas.py \
  backend/app/routers/api.py \
  backend/app/routers/cloudsea.py \
  backend/app/services/cloudsea_store.py \
  backend/app/services/prediction_feedback.py \
  backend/app/services/predictor.py \
  backend/app/services/meteo_backfill.py \
  backend/app/services/spot_loader.py \
  backend/app/services/contribute_rate_limit.py \
  backend/app/services/cache.py \
  backend/app/routers/contribute.py
do
  docker cp \"\$f\" $CONTAINER:/app/app/\${f#backend/app/}
done
tar xzf yunhai-spots.tar.gz
docker cp scenic-spots/. $CONTAINER:/app/data/scenic-spots/
mkdir -p curated-spots-patch && tar xzf yunhai-curated-spots.tar.gz -C curated-spots-patch
docker cp curated-spots-patch/curated-spots/. $CONTAINER:/app/data/cloudsea/curated-spots/
mkdir -p models-patch && tar xzf yunhai-models.tar.gz -C models-patch
docker cp models-patch/. $CONTAINER:/app/data/cloudsea/models/
rm -rf static-new && mkdir -p static-new && tar xzf yunhai-static.tar.gz -C static-new
docker cp static-new/. $CONTAINER:/app/static/
docker restart $CONTAINER
sleep 8
curl -sf http://127.0.0.1:8088/health
echo ''
curl -sf 'http://127.0.0.1:8088/api/predict/wunvshan/viewpoint/dianjiangtai?hours=48' | python3 -c \"import json,sys; d=json.load(sys.stdin); ml=d.get('location',{}).get('ml_status',{}); print('ml_active:', ml.get('ml_active'), 'mode:', ml.get('mode')); days=d.get('days') or []; print('days:', len(days))\"
"

echo ""
echo "完成。未修改服务器 docker-compose.prod.yml 及任何 env 文件。"
