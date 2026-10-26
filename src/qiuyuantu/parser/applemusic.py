"""Apple Music 网页公开封面与动态封面；不读取歌曲音频、试听或用户鉴权。"""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlparse

from parsel import Selector

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError


def page_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "music.apple.com":
        raise ParseError("unsupported", "请使用 Apple Music 歌曲、专辑或歌单链接")
    path = unquote(parsed.path)
    if any(part in {".", ".."} for part in path.split("/")) or not re.fullmatch(
        r"/[a-z]{2}/(?:album|song|playlist)/(?:[^/]+/)?(?:[0-9]+|pl\.[A-Za-z0-9_-]+)/?", path
    ):
        raise ParseError("unsupported", "请使用 Apple Music 歌曲、专辑或歌单链接")
    # 专辑链接的 ?i=歌曲ID 仍以整张专辑的封面为准，不读取该歌曲的音频。
    return "https://music.apple.com" + parsed.path.rstrip("/")


def _media(url: str, *, image: bool = False) -> str:
    parsed = urlparse(url)
    hosts = ("mzstatic.com",) if image else ("itunes.apple.com",)
    if parsed.scheme != "https" or not any(
        parsed.hostname == h or (parsed.hostname or "").endswith("." + h) for h in hosts
    ):
        raise ParseError("parse", "Apple Music 返回的封面地址不可用")
    return url


def _header(html: str) -> dict:
    raw = Selector(html).css("#serialized-server-data::text").get()
    if not raw:
        raise ParseError("parse", "Apple Music 页面没有返回封面信息")
    data = json.loads(raw)
    for record in data.get("data") or []:
        for section in (record.get("data") or {}).get("sections") or []:
            if section.get("itemKind") not in {"containerDetailHeaderLockup", "songDetailHeader"}:
                continue
            for item in section.get("items") or []:
                if item.get("artwork") and item.get("title"):
                    return item
    raise ParseError("empty", "Apple Music 没有提供这条作品的公开封面")


def artwork_variants(header: dict) -> dict[str, str]:
    dictionary = (header.get("videoArtwork") or {}).get("dictionary") or {}
    return {
        key: _media(value["video"])
        for key, value in dictionary.items()
        if isinstance(value, dict) and value.get("video")
    }


class AppleMusic(BaseParser):
    async def artwork(self, url: str) -> tuple[dict, str]:
        canonical = page_url(url)
        html = await self.get_text(canonical, follow_redirects=True)
        return _header(html), canonical

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        header, canonical = await self.artwork(share_url)
        art = header["artwork"].get("dictionary") or header["artwork"]
        cover = _media(art["url"].replace("{w}", "3000").replace("{h}", "3000").replace("{f}", "jpg"), image=True)
        variants = artwork_variants(header)
        author = " / ".join(row.get("title") or "" for row in header.get("subtitleLinks") or [])
        return VideoInfo(
            video_url="",
            cover_url=cover,
            title=header["title"],
            author=VideoAuthor(name=author),
            images=[ImgInfo(url=cover)],
            page_url=canonical,
            width=int(art.get("width") or 0),
            height=int(art.get("height") or 0),
            formats=[
                FormatInfo(
                    label="方形动态封面" if "Square" in key else "竖版动态封面",
                    format_spec="apple-artwork:" + key,
                    ext="mp4",
                )
                for key in variants
            ],
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(video_id)
