import re
from urllib.parse import urljoin, urlparse

from parsel import Selector

from ..utils import gather_or_cancel
from . import image_probe
from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, direct_format, verify_direct_formats

_COVER = re.compile(r"background-image:url\((.*)\)")
_IMAGE_SIZE = re.compile(r"/(?:n?mw[0-9]+|orj[0-9]+|bmiddle|thumbnail|large|oslarge)/")


async def _original_image(url: str) -> str:
    parsed = urlparse(url)
    if not (parsed.hostname or "").endswith(".sinaimg.cn") or not _IMAGE_SIZE.search(parsed.path):
        return url
    candidates = list(
        dict.fromkeys([url, *(_IMAGE_SIZE.sub(f"/{kind}/", url, count=1) for kind in ("original", "large"))])
    )
    sizes = await gather_or_cancel(*(image_probe.probe(candidate) for candidate in candidates))
    if not sizes[0]:
        return url
    # 相同最大尺寸时保留未缩放的 original 地址；错误占位图较小时继续用可读的预览。
    ranked = [
        (max(size), size[0] * size[1], index, candidate)
        for index, (candidate, size) in enumerate(zip(candidates, sizes, strict=True))
        if size
    ]
    return max(ranked, key=lambda row: (row[0], row[1], row[2] == 1))[3]


class LvZhou(BaseParser):
    """绿洲"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        sel = Selector(await self.get_text(share_url, follow_redirects=True))
        urls = list(dict.fromkeys(urljoin(share_url, url) for url in sel.css(".media img::attr(src)").getall()))
        if len(urls) > 64:
            raise ParseError("too_large", "绿洲图集超过 64 张图片")
        originals = await gather_or_cancel(*(_original_image(url) for url in urls))
        cover = _COVER.search(sel.css("div.video-cover::attr(style)").get(default=""))
        info = VideoInfo(
            video_url="",
            cover_url=cover.group(1) if cover else next(iter(originals), ""),
            title=sel.css(".status-text::text, .status-title::text").get(default="").strip(),
            images=[ImgInfo(url) for url in originals],
            author=VideoAuthor(
                name=sel.css("div.nickname::text").get(), avatar=sel.css("a.avatar img::attr(src)").get()
            ),
        )
        video = sel.css("video::attr(src)").get()
        formats = await verify_direct_formats([direct_format(video, {})]) if video else []
        return apply_video_formats(info, formats)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(f"https://m.oasis.weibo.cn/v1/h5/share?sid={video_id}")
