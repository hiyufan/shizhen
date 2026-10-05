#!/bin/bash
# 起 warp-svc → 首次注册（存在 /var/lib/cloudflare-warp，挂卷后重启不会重复注册）→ 代理模式连上 → socat 对外
set -euo pipefail
warp-svc --accept-tos &
cli() { warp-cli --accept-tos "$@"; }
for _ in $(seq 1 30); do cli status >/dev/null 2>&1 && break; sleep 1; done
if ! cli registration show >/dev/null 2>&1; then
  cli registration new
fi
cli mode proxy
cli proxy port 40000
cli connect
exec socat TCP-LISTEN:1080,fork,reuseaddr TCP:127.0.0.1:40000
