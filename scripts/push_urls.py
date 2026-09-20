"""把站内所有页面主动推送给百度 / 提交 sitemap 给 Bing 和 Google。

用法：
  PARSE_VIDEO_SITE_URL=https://your.domain BAIDU_PUSH_TOKEN=xxxx python scripts/push_urls.py
  # 百度 token 在 https://ziyuan.baidu.com → 普通收录 → API 提交 里
  # Bing / Google 只需在各自站长平台提交一次 sitemap，这里顺便 ping 一下
"""
from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from parse_video_py import seo  # noqa: E402

site = os.environ.get("PARSE_VIDEO_SITE_URL", "").rstrip("/")
if not site:
    sys.exit("请设置 PARSE_VIDEO_SITE_URL，例如 https://example.com")

urls = [site + p for p in seo.all_paths()]
print(f"{len(urls)} 个地址")

token = os.environ.get("BAIDU_PUSH_TOKEN")
if token:
    host = urllib.parse.urlparse(site).netloc
    req = urllib.request.Request(
        f"http://data.zz.baidu.com/urls?site={host}&token={token}",
        data="\n".join(urls).encode(), headers={"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        print("百度主动推送:", resp.read().decode())
else:
    print("未设置 BAIDU_PUSH_TOKEN，跳过百度推送")

for name, ping in (
    ("Bing", f"https://www.bing.com/ping?sitemap={urllib.parse.quote(site + '/sitemap.xml', safe='')}"),
    ("Google", f"https://www.google.com/ping?sitemap={urllib.parse.quote(site + '/sitemap.xml', safe='')}"),
):
    try:
        with urllib.request.urlopen(ping, timeout=20) as resp:
            print(f"{name} sitemap ping: {resp.status}")
    except Exception as err:  # noqa: BLE001
        print(f"{name} sitemap ping 失败: {err}")
