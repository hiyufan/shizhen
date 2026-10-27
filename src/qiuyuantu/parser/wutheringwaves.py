"""鸣潮国际服启动器的当前公开背景；按配置版本取资源，不维护历史素材。"""

import re
from urllib.parse import urlparse

from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

ROOT = "https://prod-alicdn-gamestarter.kurogame.com/launcher/"
PROJECT = "50004_obOHXFrFanqsaIEOmuKroCcbZkQRBC7c/G153/"
CONFIG_URL = ROOT + "launcher/" + PROJECT + "index.json"
MEDIA_HOSTS = {"hw-pcdownload-qcloud.aki-game.net", "pcdownload-huoshan.aki-game.net"}


def _asset(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in MEDIA_HOSTS or parsed.username or parsed.port:
        raise ParseError("parse", "鸣潮返回的壁纸地址不可用")
    return value


class WutheringWaves(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        parsed = urlparse(share_url)
        if parsed._replace(query="", fragment="").geturl() != CONFIG_URL:
            raise ParseError("unsupported", "请在鸣潮壁纸工具中选择国际服")
        return await self.parse_video_id("global")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if video_id != "global":
            raise ParseError("unsupported", "目前仅支持鸣潮国际服当前启动器壁纸")
        config = await self.get_json(CONFIG_URL)
        version = (config.get("functionCode") or {}).get("background", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", version):
            raise ParseError("parse", "鸣潮启动器暂时没有返回壁纸信息")
        background = await self.get_json(ROOT + PROJECT + "background/" + version + "/en.json")
        if background.get("functionSwitch") != 1:
            raise ParseError("empty", "鸣潮当前没有可用的公开壁纸")
        kind = background.get("backgroundFileType")
        if kind not in (1, 2):
            raise ParseError("unsupported", "鸣潮当前壁纸格式暂不支持")
        media = _asset(background.get("backgroundFile") or "")
        cover = _asset(background.get("firstFrameImage") or "") if kind == 2 else media
        if not media:
            raise ParseError("empty", "鸣潮当前没有可用的公开壁纸")
        return VideoInfo(
            video_url=media if kind == 2 else "",
            cover_url=cover,
            images=[ImgInfo(url=cover)] if cover else [],
            title="鸣潮 · 国际服启动器",
            author=VideoAuthor(name="Wuthering Waves"),
            page_url=CONFIG_URL,
        )
