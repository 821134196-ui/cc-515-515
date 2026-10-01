#!/usr/bin/env bash
# 一条本地命令启动毕业生档案转递系统（含拒收样例演示数据）
cd "$(dirname "$0")"
exec python3 app.py serve "$@"
