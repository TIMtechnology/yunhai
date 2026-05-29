#!/usr/bin/env bash
# 从生产容器拉取 cloudsea.db 到本地，供 ML 训练 / 回测评估。
set -euo pipefail

SSH="ssh -i /Users/likun/ssh_2025 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
HOST="root@182.203.168.140"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/data/cloudsea/cloudsea.prod.db"

echo "==> 从 yunhai 容器导出 cloudsea.db"
$SSH "$HOST" 'docker cp yunhai-yunhai-1:/app/data/cloudsea/cloudsea.db /tmp/cloudsea.db && ls -lh /tmp/cloudsea.db'

echo "==> 下载到 ${OUT}"
scp -i /Users/likun/ssh_2025 -o IdentitiesOnly=yes "$HOST:/tmp/cloudsea.db" "$OUT"
ls -lh "$OUT"
echo "完成。训练示例："
echo "  python3 scripts/train_cloudsea_model.py --db $OUT --approved-only --compare-terrain"
echo "  python3 scripts/eval_labeled_days.py --db $OUT --spot-id donglingshan --viewpoint-id fengding"
