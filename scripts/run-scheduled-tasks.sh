#!/usr/bin/env bash
# 云海定时任务：气象 watcher + 预测回测 reconcile
# 生产：在容器内执行（代码与 v7.1 镜像一致）；本地：直接 python3
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="${YUNHAI_CONTAINER:-yunhai-yunhai-1}"
DB="${CLOUDSEA_DB_PATH:-$ROOT/data/cloudsea/cloudsea.db}"

run_in_container() {
  echo "[$(date '+%F %T')] watch + reconcile (docker:$CONTAINER)"
  docker exec -i \
    -e CLOUDSEA_DB_PATH=/app/data/cloudsea/cloudsea.db \
    -e CLOUDSEA_ENABLED=1 \
    -e CLOUDSEA_AUTO_SNAPSHOT=1 \
    -e CLOUDSEA_WATCH_ENABLED=1 \
    "$CONTAINER" \
    python3 - <<'PY'
import json
from datetime import date, timedelta

from app.services.cloudsea_store import init_store
from app.services.forecast_watch import run_forecast_watch_sync
from app.services.prediction_feedback import reconcile_target_date

init_store()
watch = run_forecast_watch_sync()
print(json.dumps(watch, ensure_ascii=False, indent=2))
for i in range(1, 4):
    d = (date.today() - timedelta(days=i)).isoformat()
    result = reconcile_target_date(d)
    print(f"{d}: reconciled {result['reconciled']}/{result['total']}")
PY
}

run_local() {
  cd "$ROOT"
  export CLOUDSEA_DB_PATH="$DB"
  export CLOUDSEA_ENABLED="${CLOUDSEA_ENABLED:-1}"
  export CLOUDSEA_AUTO_SNAPSHOT="${CLOUDSEA_AUTO_SNAPSHOT:-1}"
  export CLOUDSEA_WATCH_ENABLED="${CLOUDSEA_WATCH_ENABLED:-1}"

  echo "[$(date '+%F %T')] watch_forecast_changes"
  python3 scripts/watch_forecast_changes.py --db "$DB"

  echo "[$(date '+%F %T')] reconcile_prediction_outcomes"
  python3 scripts/reconcile_prediction_outcomes.py --db "$DB" --days-back 3
}

if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  run_in_container
else
  run_local
fi

echo "[$(date '+%F %T')] done"
