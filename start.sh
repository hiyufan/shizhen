#!/usr/bin/env bash
# macOS / Linux 一键启动：需要 Rust 工具链（https://rustup.rs）和 PATH 上的 ffmpeg
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || echo "提示：没找到 ffmpeg，转换功能用不了（brew install ffmpeg / apt install ffmpeg）"
echo "打开 http://127.0.0.1:${PARSE_VIDEO_PORT:-8000}"
exec cargo run --release
