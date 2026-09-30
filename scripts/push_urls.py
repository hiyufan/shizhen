"""把站内所有页面主动推送给百度 / 提交 IndexNow 给 Bing、Yandex 等。

用法：
  PARSE_VIDEO_SITE_URL=https://your.domain BAIDU_PUSH_TOKEN=xxxx python scripts/push_urls.py
  # 百度 token 在 https://ziyuan.baidu.com → 普通收录 → API 提交 里
  # IndexNow 不需要账号（Bing / Yandex / Seznam / Naver 参与互通），新页面
  # 上线后重跑一次即可；Google 不参与 IndexNow，收录要走 Search Console。
"""
from __future__ import annotations

import json
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

host = urllib.parse.urlparse(site).netloc

token = os.environ.get("BAIDU_PUSH_TOKEN")
if token:
    req = urllib.request.Request(
        f"http://data.zz.baidu.com/urls?site={host}&token={token}",
        data="\n".join(urls).encode(), headers={"Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("百度主动推送:", resp.read().decode())
    except urllib.error.HTTPError as err:
        # 百度的报错明细在响应体里（token 无效 / over quota / site init fail）
        print(f"百度主动推送失败: HTTP {err.code} {err.read().decode()}")
else:
    print("未设置 BAIDU_PUSH_TOKEN，跳过百度推送")

key = seo.INDEXNOW_KEY
payload = json.dumps({
    "host": host,
    "key": key,
    "keyLocation": f"{site}/{key}.txt",
    "urlList": urls,
}).encode()
req = urllib.request.Request(
    "https://api.indexnow.org/indexnow", data=payload,
    headers={"Content-Type": "application/json; charset=utf-8"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        # 200 / 202 都算收下；422 是 key 文件校验没过，429 是提交太频繁
        print(f"IndexNow（Bing / Yandex）: HTTP {resp.status}")
except Exception as err:  # noqa: BLE001
    print(f"IndexNow 提交失败: {err}")
