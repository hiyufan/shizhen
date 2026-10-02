import base64
import re

from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError

# tv.sohu.com/v/<base64>.html，base64 解出来是 us/<uid>/<vid>.shtml
_BASE64_PATH = re.compile(r"/v/([A-Za-z0-9+/=]+)\.html")
# my.tv.sohu.com/us/<uid>/<vid>.shtml
_USER_VIDEO = re.compile(r"/?us/\d+/(\d+)\.shtml")
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
        # 优先高清，没有就用下载地址
        video_url = data.get("url_high_mp4") or data.get("download_url") or ""
        if not video_url:
            raise ParseError("empty", "搜狐视频没有给播放地址")
        user = data.get("user") or {}
        return VideoInfo(
            video_url=video_url,
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
        elif "my.tv.sohu.com" not in raw_url and "tv.sohu.com/us/" not in raw_url:
            raise ParseError("unsupported", "不是搜狐视频的作品链接")
        match = _USER_VIDEO.search(raw_url)
        if not match:
            raise ParseError("unsupported", "链接里没有搜狐视频的视频 ID")
        return match.group(1)
