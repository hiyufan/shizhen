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
# 控制口：应用发现这个出口被平台拦下时发一行 renew，换一个出口地址（见 renew.sh）。不对外发布，只在 compose 内网可达。
socat TCP-LISTEN:1081,fork,reuseaddr EXEC:/renew.sh &
exec socat TCP-LISTEN:1080,fork,reuseaddr TCP:127.0.0.1:40000
