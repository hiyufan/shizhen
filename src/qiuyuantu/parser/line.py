"""只读取 LINE 商店页面已经公开展示的贴图预览。"""

import json
import re
from urllib.parse import urlparse

from lxml import html

from .base import BaseParser, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError


def _preview_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "stickershop.line-scdn.net" or parsed.username or parsed.port:
        raise ParseError("parse", "LINE 返回的预览素材地址无效")
    return value


def _preview(row: dict) -> MediaAsset | None:
    ident = str(row.get("id", ""))
    if not re.fullmatch(r"[1-9][0-9]{0,19}", ident):
        raise ParseError("parse", "LINE 返回的贴图编号无效")
    cover = _preview_url(row["staticUrl"]) if row.get("staticUrl") else ""
    animation = row.get("animationUrl") or row.get("popupUrl")
    url = _preview_url(animation) if animation else cover
    if not url:
        return None
    return MediaAsset(url=url, label=ident, cover_url=cover, ext="png", kind="animation" if animation else "image")


class Line(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        parsed = urlparse(share_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"store.line.me", "line.me"}:
            raise ParseError("unsupported", "请粘贴 LINE 商店的贴图包链接")
        match = re.fullmatch(r"/(?:stickershop/product/|S/sticker/)([1-9][0-9]{0,19})(?:/[A-Za-z_-]+)?/?", parsed.path)
        if not match:
            raise ParseError("unsupported", "暂时支持 LINE 商店贴图包，emoji 商品请使用对应的贴图链接")
        return await self.parse_video_id(match[1])

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[1-9][0-9]{0,19}", video_id):
            raise ParseError("unsupported", "LINE 贴图包编号无效")
        page = "https://store.line.me/stickershop/product/" + video_id + "/en"
        text = await self.get_text(page, follow_redirects=True, timeout=20)
        if len(text) > 2_000_000:
            raise ParseError("parse", "LINE 页面内容过大")
        root = html.fromstring(text)
        rows = root.xpath("//*[@data-preview]/@data-preview")
        if len(rows) > 201:
            raise ParseError("restricted", "这个贴图包的预览数量超过限制")
        assets = {}
        for value in rows:
            row = json.loads(value)
            if str(row.get("id")) == video_id:
                continue  # 商店封面也是一个预览对象，不能算作正文贴图。
            if asset := _preview(row):
                assets[asset.url] = asset
        if not assets:
            raise ParseError("empty", "这个 LINE 贴图包没有可读取的公开预览")
        title = root.xpath('//meta[@property="og:title"]/@content')
        return VideoInfo(
            video_url="",
            cover_url=next(iter(assets.values())).cover_url,
            title=title[0] if title else "LINE",
            assets=list(assets.values()),
            page_url=page,
            author=VideoAuthor(name="LINE"),
            notice="这里只保存商店公开展示的预览；动画可转 GIF，不包含声音和购买后专属内容。",
        )
