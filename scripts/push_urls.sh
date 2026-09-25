#!/usr/bin/env bash
# 把站内所有页面主动推送给百度 / 提交 sitemap 给 Bing 和 Google。
#
# 用法：
#   PARSE_VIDEO_SITE_URL=https://your.domain BAIDU_PUSH_TOKEN=xxxx scripts/push_urls.sh
#   # 百度 token 在 https://ziyuan.baidu.com → 普通收录 → API 提交 里
#   # 页面清单来自 `shizhen paths`（Docker 里：docker compose exec app shizhen paths）
set -euo pipefail

site="${PARSE_VIDEO_SITE_URL:-}"
site="${site%/}"
[ -n "$site" ] || { echo "请设置 PARSE_VIDEO_SITE_URL，例如 https://example.com" >&2; exit 1; }

shizhen="${SHIZHEN:-shizhen}"
urls=$("$shizhen" paths | sed "s#^#$site#")
echo "$(echo "$urls" | wc -l) 个地址"

if [ -n "${BAIDU_PUSH_TOKEN:-}" ]; then
  host="${site#*://}"
  echo -n "百度主动推送: "
  curl -s -H "Content-Type: text/plain" --data-binary "$urls" \
    "http://data.zz.baidu.com/urls?site=${host}&token=${BAIDU_PUSH_TOKEN}"
  echo
else
  echo "未设置 BAIDU_PUSH_TOKEN，跳过百度推送"
fi

for ping in "Bing https://www.bing.com/ping" "Google https://www.google.com/ping"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -G --data-urlencode "sitemap=${site}/sitemap.xml" "${ping#* }" || true)
  echo "${ping%% *} sitemap ping: $code"
done
