import base64
import re

from .base import BaseParser, FormatInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import number

# tv.sohu.com/v/<base64>.html，base64 解出来是 us/<uid>/<vid>.shtml
_BASE64_PATH = re.compile(r"/v/([A-Za-z0-9+/=]+)\.html")
# my.tv.sohu.com/us/<uid>/<vid>.shtml；tv.sohu.com 首页上的还有 <日期>/n<vid>.shtml（base64 包着），同一个接口能查
_USER_VIDEO = re.compile(r"/?us/\d+/(\d+)\.shtml|/?\d{8}/n(\d+)\.shtml")
_API = "https://api.tv.sohu.com/v4/video/info/{vid}.json?site=2&api_key=9854b2afa779e1a6bcdd07b217417549&sver=6.2.0"


class Sohu(BaseParser):
    """搜狐视频"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(self._extract_vid(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not video_id:
            raise ParseError("unsupported", "视频 ID 不能为空")
        body = await self.get_json(_API.format(vid=video_id))
        if body.get("status") != 200:
            raise ValueError(f"搜狐视频API返回错误: {body.get('statusText', '')} (status: {body.get('status')})")
        data = body.get("data")
        if not data:
            raise ParseError("deleted", "搜狐视频没有返回这条视频")
        formats = _formats(data)
        if not formats:
            raise ParseError("empty", "搜狐视频没有给播放地址")
        best = formats[0]
        user = data.get("user") or {}
        return VideoInfo(
            video_url=best.url,
            width=best.width,
            height=best.height,
            duration=number(data.get("total_duration")),
            formats=formats[1:],
            cover_url=data.get("originalCutCover", ""),
            title=data.get("video_name", ""),
            author=VideoAuthor(
                uid=str(user.get("user_id", "")), name=user.get("nickname", ""), avatar=user.get("small_pic", "")
            ),
        )

    @staticmethod
    def _extract_vid(raw_url: str) -> str:
        if match := _BASE64_PATH.search(raw_url):
            raw_url = base64.b64decode(match.group(1)).decode("utf-8")
        elif (
            "my.tv.sohu.com" not in raw_url
            and "tv.sohu.com/us/" not in raw_url
            and not re.search(r"/\d{8}/n\d+", raw_url)
        ):
            raise ParseError("unsupported", "不是搜狐视频的作品链接")
        match = _USER_VIDEO.search(raw_url)
        if not match:
            raise ParseError("unsupported", "链接里没有搜狐视频的视频 ID")
        return match.group(1) or match.group(2)


def _formats(data: dict) -> list[FormatInfo]:
    """公开接口明确分为 original / super / high / nor；不把高清字段当作最高档。"""
    tiers = (("original", "原画"), ("super", "超清"), ("high", "高清"), ("nor", "标清"))
    formats = []
    for tier, label in tiers:
        if url := data.get(f"url_{tier}_mp4"):
            formats.append(
                FormatInfo(
                    url=url,
                    label=label,
                    width=int(number(data.get("vWidth"))) if tier == "original" else 0,
                    height=int(number(data.get("vHeight"))) if tier == "original" else 0,
                    filesize=int(number(data.get(f"file_size_{tier}"))),
                )
            )
    if not formats and data.get("download_url"):
        formats.append(FormatInfo(url=data["download_url"], label="下载视频"))
    return formats
