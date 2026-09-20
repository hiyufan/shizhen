#!/usr/bin/env bash
# macOS / Linux 一键启动：首次运行会创建 .venv 并安装依赖
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "创建虚拟环境并安装依赖（只需一次）..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e ".[web,cli]"
fi
echo "打开 http://127.0.0.1:${PARSE_VIDEO_PORT:-8000}"
exec .venv/bin/python main.py
