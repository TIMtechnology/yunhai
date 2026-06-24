#!/usr/bin/env bash
# 云海定时任务：气象 watcher + 预测回测 reconcile
# 生产 crontab 示例见 internal/DEPLOY.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DB="${CLOUDSEA_DB_PATH:-$ROOT/data/cloudsea/cloudsea.db}"
export CLOUDSEA_DB_PATH="$DB"
export CLOUDSEA_ENABLED="${CLOUDSEA_ENABLED:-1}"
export CLOUDSEA_AUTO_SNAPSHOT="${CLOUDSEA_AUTO_SNAPSHOT:-1}"
export CLOUDSEA_WATCH_ENABLED="${CLOUDSEA_WATCH_ENABLED:-1}"

echo "[$(date '+%F %T')] watch_forecast_changes"
python3 scripts/watch_forecast_changes.py --db "$DB"

echo "[$(date '+%F %T')] reconcile_prediction_outcomes"
python3 scripts/reconcile_prediction_outcomes.py --db "$DB" --days-back 3

echo "[$(date '+%F %T')] done"
