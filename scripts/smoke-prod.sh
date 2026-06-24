#!/usr/bin/env bash
# 生产冒烟：验证 health、ML、V7.1 代码特征（经 SSH 在容器内检查）
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/load-deploy-env.sh
source "$(dirname "$0")/lib/load-deploy-env.sh"

BASE="${SMOKE_BASE_URL:-https://yunhai.timkj.com}"
SSH=(ssh -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

echo "== 1. /health =="
curl -sf "$BASE/health" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='ok', d; print('ok')"

echo "== 2. 五女山 ML + 日出窗概率 =="
curl -sf "$BASE/api/predict/wunvshan/viewpoint/dianjiangtai?hours=48" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ml=d.get('location',{}).get('ml_status',{})
print('ml_active', ml.get('ml_active'), 'mode', ml.get('mode'))
assert ml.get('ml_active'), ml.get('message')
days=d.get('days') or []
assert days, 'no days'
print('predict ok, days', len(days))
"

echo "== 3. 容器内 V7.1 代码特征 =="
"${SSH[@]}" "$YUNHAI_HOST" "docker exec ${YUNHAI_CONTAINER:-yunhai-yunhai-1} sh -c '
  test \$(grep -c is_ground_fog_proxy /app/app/engine/cloudsea_scorer.py) -ge 1
  test \$(grep -c supplement_precursor_rows /app/app/services/predictor.py) -ge 1
  test \$(grep -c ground_fog_proxy /app/app/engine/cloudsea_features.py) -ge 1
  wc -l /app/app/engine/cloudsea_scorer.py /app/app/services/predictor.py
  python3 -c \"import pickle; p=pickle.load(open(\\\"/app/data/cloudsea/models/spot_wunvshan_dianjiangtai.pkl\\\",\\\"rb\\\")); fn=p.get(\\\"feature_names\\\",[]); assert \\\"ground_fog_proxy\\\" in fn; print(\\\"model v7.1 features\\\", len(fn))\"
'"

echo "== 4. 社区精选 cs_* =="
curl -sf "$BASE/api/spots/search?q=&curated_only=true" | python3 -c "
import json,sys
d=json.load(sys.stdin)
cs=[x for x in d['results'] if x['id'].startswith('cs_')]
print('cs_ spots', len(cs))
"

echo ""
echo "冒烟测试通过。"
