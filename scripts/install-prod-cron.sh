#!/usr/bin/env bash
# 在生产宿主机安装定时任务（仅同步脚本 + crontab，不修改 compose / .env）
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=lib/load-deploy-env.sh
source "$(dirname "$0")/lib/load-deploy-env.sh"

REMOTE_DIR="${YUNHAI_REMOTE_DIR:-/opt/yunhai/patch}"
COMPOSE_DIR="${YUNHAI_COMPOSE_DIR:-/opt/yunhai}"
SCRIPTS_DIR="${YUNHAI_SCRIPTS_DIR:-$COMPOSE_DIR/scripts}"
DB_PATH="${YUNHAI_CLOUDSEA_DB:-$COMPOSE_DIR/data/cloudsea/cloudsea.db}"
CRON_LOG="${YUNHAI_CRON_LOG:-/var/log/yunhai-cron.log}"

SCP=(scp -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
SSH=(ssh -i "$YUNHAI_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
HOST="$YUNHAI_HOST"

echo "同步定时脚本 → $HOST:$SCRIPTS_DIR/"
"${SSH[@]}" "$HOST" "mkdir -p '$SCRIPTS_DIR'"
"${SCP[@]}" \
  scripts/run-scheduled-tasks.sh \
  scripts/watch_forecast_changes.py \
  scripts/reconcile_prediction_outcomes.py \
  "$HOST:$SCRIPTS_DIR/"

echo "安装 crontab（不触碰 docker-compose / .env）…"
"${SSH[@]}" "$HOST" "set -e
chmod +x '$SCRIPTS_DIR/run-scheduled-tasks.sh'
MARK='# yunhai-scheduled-tasks'
LINE='*/30 * * * * YUNHAI_CONTAINER=${YUNHAI_CONTAINER:-yunhai-yunhai-1} $SCRIPTS_DIR/run-scheduled-tasks.sh >> $CRON_LOG 2>&1'
( crontab -l 2>/dev/null | grep -v \"\$MARK\" || true
  echo \"\$LINE \$MARK\"
) | crontab -
echo '当前 crontab 条目:'
crontab -l | grep yunhai || true
"

echo "完成。日志: $CRON_LOG"
