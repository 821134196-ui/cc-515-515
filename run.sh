#!/usr/bin/env bash
# 一条命令启动：重置演示库 -> 播种（含拒收样例李雪）-> 启动本地服务
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

export HOST PORT
$PYTHON -m app.seed
echo
exec $PYTHON -m app.server
