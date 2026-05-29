#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "内部统计看板: http://127.0.0.1:8765/analytics-dashboard.html"
echo "请先配置 config.local.json（复制 config.example.json）"
python3 -m http.server 8765
