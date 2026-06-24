#!/usr/bin/env bash
# 加载本地部署配置（scripts/deploy.local.env，已 gitignore，勿提交）
set -euo pipefail

_deploy_env_file="${DEPLOY_ENV_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/deploy.local.env}"

if [[ -f "$_deploy_env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$_deploy_env_file"
  set +a
fi

require_deploy_var() {
  local name=$1
  if [[ -z "${!name:-}" ]]; then
    echo "错误: 未设置 ${name}" >&2
    echo "请复制 scripts/deploy.local.env.example → scripts/deploy.local.env 并填写（该文件不会进入 Git）" >&2
    exit 1
  fi
}

require_deploy_var YUNHAI_SSH_KEY
require_deploy_var YUNHAI_HOST

if [[ ! -f "$YUNHAI_SSH_KEY" ]]; then
  echo "错误: SSH 密钥不存在: ${YUNHAI_SSH_KEY}" >&2
  exit 1
fi

REMOTE_DIR="${YUNHAI_REMOTE_DIR:-/opt/yunhai/patch}"
CONTAINER="${YUNHAI_CONTAINER:-yunhai-yunhai-1}"
