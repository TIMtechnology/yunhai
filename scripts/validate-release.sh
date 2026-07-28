#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if git ls-files | grep -Eq '(^|/).*\.(db|sqlite)(-|\.|$)|data/cloudsea/models/.*\.pkl$'; then
  echo "错误: Git 中仍包含数据库快照或 pickle 模型" >&2
  exit 1
fi

unexpected_models="$(
  find data/cloudsea/models -type f \
    ! -name 'spot_wunvshan_dianjiangtai.json' \
    -print
)"
if [[ -n "$unexpected_models" ]]; then
  echo "错误: 模型目录含未列入受审清单的文件:" >&2
  echo "$unexpected_models" >&2
  exit 1
fi

if ! grep -q 'CLOUDSEA_ML_ENABLED=true' docker-compose.deploy.yml; then
  echo "错误: 已通过门禁的点位模型未在生产模板中启用" >&2
  exit 1
fi

PYTHONPATH=backend .venv/bin/python - <<'PY'
from pathlib import Path

from app.engine.operational_ml import load_safe_artifact

path = Path("data/cloudsea/models/spot_wunvshan_dianjiangtai.json")
artifact = load_safe_artifact(
    path,
    expected_spot_id="wunvshan",
    expected_viewpoint_id="dianjiangtai",
)
assert artifact["evaluation"]["quality_gate"]["passed"] is True
print("受审 ML JSON 门禁通过")
PY

PYTHONPATH=backend .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q backend/app scripts
.venv/bin/pip-audit -r backend/requirements.lock --no-deps --disable-pip --progress-spinner off
(
  cd frontend
  npm audit --audit-level=high
  npm run build
)

echo "发布门禁通过。"
