#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if git ls-files | grep -Eq '(^|/).*\.(db|sqlite)(-|\.|$)|data/cloudsea/models/.*\.pkl$'; then
  echo "错误: Git 中仍包含数据库快照或 pickle 模型" >&2
  exit 1
fi

if ! grep -q 'CLOUDSEA_ML_ENABLED=false' docker-compose.deploy.yml; then
  echo "错误: 未通过质量门禁的 ML 不得在生产模板中启用" >&2
  exit 1
fi

PYTHONPATH=backend .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q backend/app scripts
.venv/bin/pip-audit -r backend/requirements.lock --no-deps --disable-pip --progress-spinner off
(
  cd frontend
  npm audit --audit-level=high
  npm run build
)

echo "发布门禁通过。"
