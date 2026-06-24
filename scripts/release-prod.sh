#!/usr/bin/env bash
# 标准生产发版：构建镜像 → 上传 → compose 重启 → 冒烟测试
# 用法：bash scripts/release-prod.sh
#       SKIP_TRAIN=1 bash scripts/release-prod.sh   # 跳过训练，使用已有 .pkl
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 0. 可选：训练 ML v7 点位模型 =="
if [[ "${SKIP_TRAIN:-0}" != "1" ]]; then
  python3 scripts/train_cloudsea_model.py \
    --db data/cloudsea/cloudsea.prod.db \
    --spot-id wunvshan --viewpoint-id dianjiangtai \
    --window v7 --mode oracle --enhanced --db-only
  python3 scripts/train_cloudsea_model.py \
    --db data/cloudsea/cloudsea.prod.db \
    --spot-id donglingshan --viewpoint-id fengding \
    --window v7 --mode oracle --enhanced --db-only
else
  echo "SKIP_TRAIN=1，使用 data/cloudsea/models/ 现有 .pkl"
fi

for f in data/cloudsea/models/spot_wunvshan_dianjiangtai.pkl data/cloudsea/models/spot_donglingshan_fengding.pkl; do
  if [[ ! -f "$f" ]]; then
    echo "错误: 缺少模型 $f（去掉 SKIP_TRAIN 或手动训练）" >&2
    exit 1
  fi
done

echo "== 1. 构建 amd64 镜像 =="
bash scripts/build-amd64.sh

echo "== 2. 部署到生产 =="
bash scripts/deploy-prod.sh yunhai-amd64.tar

echo "== 3. 冒烟测试 =="
bash scripts/smoke-prod.sh

echo ""
echo "标准发版完成。"
