"""今日头条微头条（m.toutiao.com/w/<id>）：取帖子里的全部图片、正文标题和作者。

App「复制链接」给的是 m.toutiao.com/is/<码>/ 短链，302 到 /w/<id>/?…分享参数。
手机版页面把整条帖子放在 <script id="RENDER_DATA"> 里（URL 编码的 JSON）；桌面版 www.toutiao.com
回的是脚本验证页，拿不到数据，所以一律按作品 ID 去请求手机版。

海外服务器直连手机版页面、图片和视频 CDN 都正常（2026-11 实测），所以不在 utils.CN_SOURCES 里，
不走国内代理 / 中继。

图片地址带签名（x-signature），只有页面给的那一档能下：largeImageList 是帖子里最大的一档，
去掉处理参数拿「原图」的写法（/origin/、~noop）全部 403（2026-11 实测）。CDN 不校验 Referer。

头条视频（/video/<id>）交给 yt-dlp 的头条提取器，但它只认 www.toutiao.com/video/<id>/，
App 短链跳到的手机版 m.toutiao.com/video/<id>/ 要先换成这个写法。
头条文章（/article/）暂不支持，直接说明，不让用户以为是链接本身有问题。
"""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urljoin, urlparse

from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

_POST = re.compile(r"/w/(\d{6,25})/?")
_VIDEO = re.compile(r"/video/(\d{6,25})/?")
_ARTICLE = re.compile(r"/(?:article|a|i|group)/?\d{6,25}")
_RENDER_DATA = re.compile(r'<script id="RENDER_DATA" type="application/json">(.*?)</script>', re.S)
_UNSUPPORTED = "目前支持头条视频和微头条图文；头条文章暂不支持"


def post_id(url: str) -> str:
    match = _POST.search(urlparse(url).path)
    return match.group(1) if match else ""


def _render_data(html: str) -> dict:
    match = _RENDER_DATA.search(html)
    if not match:
        raise ParseError("parse", "页面里没找到微头条数据")
    return json.loads(unquote(match.group(1)))


def _images(base: dict) -> list[ImgInfo]:
    images = []
    for item in base.get("largeImageList") or base.get("thumbImageList") or []:
        candidates = [item.get("url"), *((u or {}).get("url") for u in item.get("urlList") or [])]
        url = next((u for u in candidates if u), "")
        if url:
            images.append(ImgInfo(url=url))
    return images


class TouTiao(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        url = share_url
        if urlparse(url).path.startswith("/is/"):
            # 短链：不跟跳转，只读 Location
            location = await self.redirect_target(url)
            if not location:
                raise ParseError("deleted", "头条短链没有跳转到作品页")
            url = urljoin(url, location)
        if ident := post_id(url):
            return await self.parse_video_id(ident)
        path = urlparse(url).path
        if video := _VIDEO.search(path):
            return await self._video(video.group(1))
        if _ARTICLE.search(path):
            raise ParseError("unsupported", _UNSUPPORTED)
        raise ParseError("unsupported", "链接里没有头条作品的 ID")

    async def _video(self, video_id: str) -> VideoInfo:
        from .ytdlp import YtDlp

        info = await YtDlp().parse_share_url(f"https://www.toutiao.com/video/{video_id}/")
        info.source = "toutiao"
        return info

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        page = f"https://m.toutiao.com/w/{video_id}/"
        data = _render_data(await self.get_text(page, follow_redirects=True))
        article = data.get("articleInfo") or {}
        base = (article.get("thread") or {}).get("threadBase") or {}
        if not base:
            raise ParseError("deleted", "这条微头条已删除或不可见")
        images = _images(base)
        if not images:
            # 帖子里只有视频（videoGroup）或纯文字：微头条里的视频没有单独的播放地址，先如实说明
            if base.get("videoGroup"):
                raise ParseError("unsupported", "微头条里的视频暂不支持，可以打开视频本身的分享链接")
            raise ParseError("empty", "这条微头条没有图片")
        user = (base.get("user") or {}).get("info") or {}
        title = (base.get("title") or base.get("content") or "").strip()
        return VideoInfo(
            video_url="",
            cover_url=images[0].url,
            title=title[:120],
            images=images,
            author=VideoAuthor(
                uid=str(user.get("userId") or ""), name=user.get("name", ""), avatar=user.get("avatarUrl", "")
            ),
            page_url=page,
        )
