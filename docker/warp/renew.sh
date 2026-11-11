#!/bin/bash
# 控制口（entrypoint.sh 里的 1081）每来一个连接跑一次：第一行是 renew 就断开重连，让 WARP 换一个出口地址，
# 然后回一行 ok / recent / failed。平台按地址拦这个出口时由应用触发（convert/fallback_exit.py），
# 重连一两秒，期间经它的连接会断，所以这里再限一道最短间隔，应用那边另有递增的间隔。
set -uo pipefail

read -r -t 5 cmd || exit 0
[ "${cmd%$'\r'}" = "renew" ] || exit 0

exec 9>/run/warp-renew.lock
flock -w 30 9 || { echo failed; exit 0; }

stamp=/run/warp-renew.stamp
now=$(date +%s)
if [ -f "$stamp" ] && [ $((now - $(cat "$stamp"))) -lt 20 ]; then
  echo recent
  exit 0
fi

cli() { warp-cli --accept-tos "$@" >/dev/null 2>&1; }
cli disconnect
cli connect
for _ in $(seq 1 30); do
  if curl -fsS -m 3 -x http://127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | grep -q "warp=on"; then
    date +%s > "$stamp"
    echo "warp: 已重连换出口" >&2
    echo ok
    exit 0
  fi
  sleep 0.5
done
echo "warp: 重连后一直没连上" >&2
echo failed
