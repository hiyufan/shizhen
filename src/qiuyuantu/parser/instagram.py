"""Instagram 图片帖 / 多图帖：yt-dlp 只认视频，图片那几项一律报 No video formats found，
整条帖子就失败了（issue #5：一条 20 张图的帖子）。

未登录打开 /p/{code} 时，页面内嵌的 JSON 里就有整条帖子（xig_polaris_media.if_not_gated_logged_out），
多图帖每一项的 image_versions2.candidates 第一个就是原图（实测 3072x4096）。做法和 yt-dlp 一样：
先开一次首页拿 csrftoken / mid 这些 cookie，再开帖子页。
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from ..utils import create_async_client
from .base import ImgInfo, VideoAuthor, VideoInfo

_POST_PATH = re.compile(r"^/(?:[\w.]+/)?p/([\w-]+)")
_JSON_SCRIPT = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-us,en;q=0.5",
    "Sec-Fetch-Mode": "navigate",
}


def post_code(url: str) -> str:
    """instagram.com/p/{code} 或 instagram.com/{用户}/p/{code} 的 code，不是帖子返回空串。"""
    parsed = urlparse(url)
    if not (parsed.hostname or "").endswith("instagram.com"):
        return ""
    m = _POST_PATH.match(parsed.path)
    return m.group(1) if m else ""


async def image_post(url: str) -> VideoInfo | None:
    """拿不到（不是帖子、要登录、页面改版）返回 None，由调用方报原来的错。"""
    code = post_code(url)
    if not code:
        return None
    async with create_async_client(timeout=20) as client:
        await client.get("https://www.instagram.com/", headers=_HEADERS)
        resp = await client.get(f"https://www.instagram.com/p/{code}", headers=_HEADERS)
    post = find_post(resp.text, code)
    return to_video_info(post, code) if post else None


def find_post(html: str, code: str) -> dict | None:
    """页面里还有作者的其它帖子（推荐区），按 code 认出这一条。"""
    for blob in _JSON_SCRIPT.findall(html):
        if "xig_polaris_media" not in blob:
            continue
        try:
            media = _find_key(json.loads(blob), "xig_polaris_media")
        except ValueError:
            continue
        post = (media or {}).get("if_not_gated_logged_out") or {}
        if post.get("code") == code:
            return post
    return None


def _find_key(obj, key: str):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        if (found := _find_key(child, key)) is not None:
            return found
    return None


def to_video_info(post: dict, code: str) -> VideoInfo | None:
    items = post.get("carousel_media") or [post]
    images = [ImgInfo(url=url) for item in items if item.get("media_type") == 1 and (url := _original(item))]
    # 图和视频混着的多图帖：结果里只放得下一个视频，取第一个
    videos = [v[0]["url"] for item in items if item.get("media_type") == 2 and (v := item.get("video_versions"))]
    if not images and not videos:
        return None
    user = post.get("user") or {}
    return VideoInfo(
        title=((post.get("caption") or {}).get("text") or "").strip()[:80],
        video_url=videos[0] if videos else "",
        cover_url=images[0].url if images else _original(items[0]),
        images=images,
        author=VideoAuthor(uid=str(user.get("pk") or ""), name=user.get("username") or "", avatar=""),
        source="instagram",
        page_url=f"https://www.instagram.com/p/{code}/",
    )


def _original(item: dict) -> str:
    """candidates 第一个是原图，后面是各种缩小 / 裁成方形的版本。"""
    candidates = (item.get("image_versions2") or {}).get("candidates") or []
    return candidates[0].get("url", "") if candidates else ""
