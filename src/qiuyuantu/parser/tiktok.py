"""TikTok 图文（photo mode）：yt-dlp 的 TikTok 提取器只认 /video/，图文帖 /@用户/photo/{id} 一律报
Unsupported URL；vt.tiktok.com、tt.site 这类短链跳到图文时也一样。

图文页 /photo/{id} 的 HTML 里没有作品数据（打开后由前端再拉）；同一个 ID 换成 /@用户/video/{id} 打开，
内嵌的 __UNIVERSAL_DATA_FOR_REHYDRATION__ 里 itemStruct.imagePost 就是整组原图，签名直链不要 cookie。
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from ..utils import create_async_client
from .base import ImgInfo, VideoAuthor, VideoInfo

# 部分短链落到用户名为空的 /@/photo/<id>，帖子页同样接受空用户名
_PHOTO_PATH = re.compile(r"^/@([^/]*)/photo/(\d+)")
_SHORT_HOSTS = ("vt.tiktok.com", "vm.tiktok.com", "tt.site")
_DATA = re.compile(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)
_MAX_HOPS = 3
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-us,en;q=0.5",
}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _on_tiktok(url: str) -> bool:
    host = _host(url)
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def photo_post(url: str) -> tuple[str, str] | None:
    """tiktok.com/@{用户}/photo/{id} 的 (用户, id)，不是图文帖返回 None。"""
    if not _on_tiktok(url):
        return None
    m = _PHOTO_PATH.match(urlparse(url).path)
    return (m.group(1), m.group(2)) if m else None


def is_short(url: str) -> bool:
    host = _host(url)
    if any(host == short or host.endswith("." + short) for short in _SHORT_HOSTS):
        return True
    return _on_tiktok(url) and urlparse(url).path.startswith("/t/")


async def _expand(client, url: str) -> str:
    """短链跟到落地地址。只跟跳转、不取正文：短链跳到视频时这一步就该停下，交回原来的错误。"""
    for _ in range(_MAX_HOPS):
        if not is_short(url):
            break
        resp = await client.get(url, follow_redirects=False)
        location = resp.headers.get("location")
        if not location:
            break
        url = urljoin(url, location)
    return url


async def image_post(url: str) -> VideoInfo | None:
    """不是图文、页面改版或拿不到数据时返回 None，由调用方报原来的错。"""
    if not (photo_post(url) or is_short(url)):
        return None
    async with create_async_client(timeout=20, headers=_HEADERS) as client:
        found = photo_post(await _expand(client, url))
        if not found:
            return None
        user, post_id = found
        resp = await client.get(f"https://www.tiktok.com/@{user}/video/{post_id}")
    item = find_item(resp.text, post_id)
    return to_video_info(item, user, post_id) if item else None


def find_item(html: str, post_id: str) -> dict | None:
    m = _DATA.search(html)
    if not m:
        return None
    try:
        scope = json.loads(m.group(1)).get("__DEFAULT_SCOPE__") or {}
    except ValueError:
        return None
    item = (((scope.get("webapp.video-detail") or {}).get("itemInfo") or {}).get("itemStruct")) or {}
    return item if item.get("id") == post_id and item.get("imagePost") else None


def _first_url(image: dict) -> str:
    return next(iter((image.get("imageURL") or {}).get("urlList") or []), "")


def to_video_info(item: dict, user: str, post_id: str) -> VideoInfo | None:
    images = [ImgInfo(url=url) for image in item["imagePost"].get("images") or [] if (url := _first_url(image))]
    if not images:
        return None
    author = item.get("author") or {}
    user = user or author.get("uniqueId") or ""
    return VideoInfo(
        title=(item.get("desc") or "").strip()[:80],
        video_url="",
        cover_url=images[0].url,
        music_url=(item.get("music") or {}).get("playUrl") or "",
        images=images,
        author=VideoAuthor(
            uid=str(author.get("id") or ""),
            name=author.get("nickname") or author.get("uniqueId") or user,
            avatar=author.get("avatarThumb") or "",
        ),
        source="tiktok",
        page_url=f"https://www.tiktok.com/@{user}/photo/{post_id}",
    )
