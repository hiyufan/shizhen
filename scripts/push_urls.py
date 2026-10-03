"""把站内所有页面主动推送给百度 / 提交 IndexNow 给 Bing、Yandex 等。

用法：
  PARSE_VIDEO_SITE_URL=https://your.domain BAIDU_PUSH_TOKEN=xxxx python scripts/push_urls.py [路径 ...]
  # 不带路径推全站；带路径只推这几个（百度额度用完后第二天补推剩下的）
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

paths = sys.argv[1:] or seo.all_paths()  # all_paths 按重要程度排好：首页、各平台页、教程
urls = [site + p for p in paths]
print(f"{len(urls)} 个地址")

host = urllib.parse.urlparse(site).netloc


def baidu_push(batch: list[str], token: str) -> dict:
    req = urllib.request.Request(
        f"http://data.zz.baidu.com/urls?site={host}&token={token}",
        data="\n".join(batch).encode(),
        headers={"Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        # 百度的报错明细在响应体里（token 无效 / over quota / site init fail）
        return json.loads(err.read() or b"{}") | {"http": err.code}


def baidu_push_within_quota(token: str) -> None:
    """新站每天只有 10 个左右的额度，一批超了整批都被拒（over quota）。
    先推第一个拿到当天剩余额度，再按顺序推满，推不完的列出来第二天带上路径补推。"""
    first = baidu_push(urls[:1], token)
    if "remain" not in first:
        print("百度主动推送失败:", first)
        return
    rest = urls[1:]
    take = rest[: first["remain"]]
    second = baidu_push(take, token) if take else {"success": 0}
    print(
        f"百度主动推送: 成功 {first.get('success', 0) + second.get('success', 0)} 个",
        second if "error" in second else "",
    )
    left = rest[len(take) :] if "error" not in second else rest
    if left:
        print(
            "今天额度用完，明天补推：python scripts/push_urls.py "
            + " ".join(urllib.parse.urlparse(u).path for u in left)
        )


token = os.environ.get("BAIDU_PUSH_TOKEN")
if token:
    baidu_push_within_quota(token)
else:
    print("未设置 BAIDU_PUSH_TOKEN，跳过百度推送")

key = seo.INDEXNOW_KEY
payload = json.dumps(
    {
        "host": host,
        "key": key,
        "keyLocation": f"{site}/{key}.txt",
        "urlList": urls,
    }
).encode()
req = urllib.request.Request(
    "https://api.indexnow.org/indexnow",
    data=payload,
    headers={"Content-Type": "application/json; charset=utf-8"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        # 200 / 202 都算收下；422 是 key 文件校验没过，429 是提交太频繁
        print(f"IndexNow（Bing / Yandex）: HTTP {resp.status}")
except Exception as err:  # noqa: BLE001
    print(f"IndexNow 提交失败: {err}")
