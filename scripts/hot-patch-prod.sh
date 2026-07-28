#!/usr/bin/env bash
set -euo pipefail

echo "错误: 热补丁会绕过依赖锁、镜像复现、备份和自动回滚，已停用。" >&2
echo "请提交修复并运行: bash scripts/release-prod.sh" >&2
exit 1
