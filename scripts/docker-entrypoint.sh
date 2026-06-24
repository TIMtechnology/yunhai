#!/bin/sh
# 标准启动：将镜像内 baked 资源同步到 cloudsea 数据卷，再启动 uvicorn
set -eu

BAKED=/app/baked
DATA=/app/data/cloudsea
STAMP_FILE="$DATA/.image_build_id"
IMAGE_ID="$(cat /app/BUILD_ID 2>/dev/null || echo unknown)"

mkdir -p "$DATA/models" "$DATA/curated-spots"

force_sync=0
if [ ! -f "$STAMP_FILE" ] || [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" != "$IMAGE_ID" ]; then
  force_sync=1
fi

sync_pkls() {
  src_dir=$1
  dest_dir=$2
  [ -d "$src_dir" ] || return 0
  mkdir -p "$dest_dir"
  for f in "$src_dir"/*.pkl; do
    [ -f "$f" ] || continue
    dest="$dest_dir/$(basename "$f")"
    if [ "$force_sync" = 1 ] || [ ! -f "$dest" ] || [ "$f" -nt "$dest" ]; then
      cp "$f" "$dest"
      echo "[entrypoint] model → $dest"
    fi
  done
}

sync_json_dir() {
  src_dir=$1
  dest_dir=$2
  [ -d "$src_dir" ] || return 0
  mkdir -p "$dest_dir"
  for f in "$src_dir"/*.json; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    case "$base" in _*) continue ;; esac
    dest="$dest_dir/$base"
    if [ "$force_sync" = 1 ] || [ ! -f "$dest" ] || [ "$f" -nt "$dest" ]; then
      cp "$f" "$dest"
      echo "[entrypoint] curated-spot → $dest"
    fi
  done
}

sync_pkls "$BAKED/models" "$DATA/models"
sync_json_dir "$BAKED/curated-spots" "$DATA/curated-spots"

printf '%s' "$IMAGE_ID" > "$STAMP_FILE"
echo "[entrypoint] BUILD_ID=$IMAGE_ID force_sync=$force_sync"

exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers "${UVICORN_WORKERS:-2}"
