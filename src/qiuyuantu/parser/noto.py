"""Google 官方 Noto 动态 emoji 文件入口，保留 CC BY 4.0 署名。"""

import re
from urllib.parse import urlparse

import httpx

from .base import BaseParser, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError

ROOT = "https://fonts.gstatic.com/s/e/notoemoji/latest/"
_CODE = r"[0-9a-f]{4,6}(?:_[0-9a-f]{4,6}){0,12}"


def _code(value: str) -> str:
    if not re.fullmatch(_CODE, value):
        raise ParseError("unsupported", "请使用官方 Noto 动态 emoji 素材链接")
    points = [int(item, 16) for item in value.split("_")]
    if any(point > 0x10FFFF or 0xD800 <= point <= 0xDFFF for point in points):
        raise ParseError("unsupported", "emoji 的 Unicode 编号无效")
    return value


class NotoEmoji(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        parsed = urlparse(share_url)
        match = re.fullmatch(
            rf"/s/e/notoemoji/latest/({_CODE})/(?:512\.(?:webp|gif)|lottie\.json|emoji\.svg)", parsed.path
        )
        if parsed.scheme != "https" or parsed.hostname != "fonts.gstatic.com" or not match:
            raise ParseError("unsupported", "请从 Noto 官方动画图库复制 WebP、GIF 或 Lottie 素材链接")
        return await self.parse_video_id(match[1])

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        code = _code(video_id)
        url = ROOT + code + "/512.webp"
        response = await self.fetch("HEAD", url, timeout=15)
        if "image/" not in response.headers.get("content-type", ""):
            raise ParseError("empty", "这个 emoji 没有可用的官方动态文件")
        title = "".join(chr(int(item, 16)) for item in code.split("_")) + " · Noto Emoji"
        assets = [MediaAsset(url=url, label="WebP", kind="animation", cover_url=url, ext="webp")]
        for name, label, kind, extension in (
            ("512.gif", "GIF", "animation", "gif"),
            ("lottie.json", "Lottie", "file", "json"),
        ):
            variant = ROOT + code + "/" + name
            try:
                await self.fetch("HEAD", variant, timeout=15)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
            assets.append(MediaAsset(url=variant, label=label, kind=kind, cover_url=url, ext=extension))
        return VideoInfo(
            video_url="",
            cover_url=url,
            title=title,
            assets=assets,
            page_url=url,
            author=VideoAuthor(name="Google Noto Emoji"),
            notice=(
                "Animated Noto Emoji © Google，CC BY 4.0："
                "https://creativecommons.org/licenses/by/4.0/。WebP 可转换；Lottie 保留原文件。"
            ),
        )
