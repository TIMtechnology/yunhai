#!/usr/bin/env bash
# 从生产容器拉取 cloudsea.db 到本地，供 ML 训练 / 回测评估。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/load-deploy-env.sh
source "$(dirname "$0")/lib/load-deploy-env.sh"

OUT="${ROOT}/data/cloudsea/cloudsea.prod.db"
SSH=(ssh -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
SCP=(scp -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
HOST="$YUNHAI_HOST"

echo "==> 从 yunhai 容器导出 cloudsea.db"
"${SSH[@]}" "$HOST" "docker cp ${CONTAINER}:/app/data/cloudsea/cloudsea.db /tmp/cloudsea.db && ls -lh /tmp/cloudsea.db"

echo "==> 下载到 ${OUT}"
"${SCP[@]}" "$HOST:/tmp/cloudsea.db" "$OUT"
ls -lh "$OUT"
echo "完成。训练示例："
echo "  python3 scripts/train_cloudsea_model.py --db $OUT --approved-only --compare-terrain"
echo "  python3 scripts/eval_labeled_days.py --db $OUT --spot-id donglingshan --viewpoint-id fengding"
