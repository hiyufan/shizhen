"""Pixiv Sketch 的匿名公开帖子与作者目录。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse

from .base import BaseParser, ContentEntry, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

ROOT = "https://sketch.pixiv.net"


def image_url(url: str) -> str:
    if not isinstance(url, str):
        return ""
    p = urlparse(url)
    return url if p.scheme == "https" and p.hostname == "img-sketch.pixiv.net" and not p.username and not p.port else ""


class PixivSketch(BaseParser):
    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua("windows"), "Referer": ROOT + "/", "Accept": "application/vnd.sketch-v4+json"}

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        p = urlparse(share_url)
        if p.scheme not in {"http", "https"} or p.hostname != "sketch.pixiv.net" or p.username or p.port:
            raise ParseError("unsupported")
        if match := re.fullmatch(r"/@([A-Za-z0-9_-]+)/?", p.path):
            return await self.profile(match[1], parse_qs(p.query))
        if match := re.fullmatch(r"/items/([A-Za-z0-9_-]{1,64})/?", p.path):
            return await self.parse_video_id(match[1])
        raise ParseError("unsupported", "请复制 Sketch 公开帖子或作者链接")

    async def request_data(self, path: str, **kw) -> dict:
        data = await self.get_json(ROOT + path, **kw)
        if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
            raise ParseError("restricted", "Sketch 未提供这条公开内容")
        return data

    async def profile(self, name: str, query: dict) -> VideoInfo:
        params = {}
        if marker := query.get("max_id", [""])[0]:
            if not re.fullmatch(r"[0-9]{1,20}", marker):
                raise ParseError("unsupported", "目录页码无效")
            params["max_id"] = marker
        body = await self.request_data(
            "/api/walls/@" + name + "/posts/public.json",
            params=params,
            headers={**self.get_default_headers(), "X-Requested-With": ROOT + "/@" + name},
        )
        rows = body["data"].get("items") or []
        if len(rows) > 100:
            raise ParseError("too_large")
        info = VideoInfo("", "", title="Sketch · @" + name, content_kind="directory")
        for post in rows:
            if _public(post):
                ident = str(post.get("id") or "")
                if not ident.isdecimal():
                    continue
                photos = post.get("media") or []
                cover = image_url((photos[0].get("photo") or {}).get("original", {}).get("url")) if photos else ""
                info.entries.append(ContentEntry(ROOT + "/items/" + ident, str(post.get("text") or ident)[:200], cover))
        following = ((body.get("_links") or {}).get("next") or {}).get("href", "")
        target = urlparse(following)
        if target.path == "/api/walls/@" + name + "/posts/public.json":
            marker = parse_qs(target.query).get("max_id", [""])[0]
            if re.fullmatch(r"[0-9]{1,20}", marker):
                info.next_page_url = ROOT + "/@" + name + "?" + urlencode({"max_id": marker})
        return info

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
            raise ParseError("unsupported")
        body = await self.request_data("/api/items/" + video_id)
        post = body["data"].get("item") or body["data"]
        if not _public(post) or (video_id.isdecimal() and str(post.get("id")) != video_id):
            raise ParseError("restricted", "帖子身份或访问权限不匹配")
        images = []
        for photo in post.get("media") or []:
            url = image_url((photo.get("photo") or {}).get("original", {}).get("url"))
            if url:
                images.append(ImgInfo(url))
        if not images:
            raise ParseError("empty")
        user = post.get("user") or {}
        return VideoInfo(
            "",
            images[0].url,
            title=str(post.get("text") or "Pixiv Sketch")[:200],
            images=images,
            author=VideoAuthor(str(user.get("id") or ""), str(user.get("name") or "")),
            page_url=ROOT + "/items/" + video_id,
        )


def _public(post: dict) -> bool:
    return post.get("is_public") is True and not any(post.get(key) for key in ("is_r18", "is_r15", "is_adult"))
