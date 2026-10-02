import re

from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError

# 央视网页面里嵌的视频 GUID
_GUID = re.compile(r'var\s+guid\s*=\s*"([^"]+)"')


class CCTV(BaseParser):
    """央视网"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(self._extract_guid_from_html(await self.get_text(share_url)))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not video_id:
            raise ParseError("unsupported", "视频 GUID 不能为空")
        data = await self.get_json(f"https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid={video_id}")
        if data.get("status", "") != "001":
            raise ValueError(
                f"央视网视频API返回错误 (status: {data.get('status', '')}, title: {data.get('title', '')})"
            )
        # manifest 里 h5e / enc / enc2 的高码率流在 H.264 帧级加扰，播放花屏，只有 hls_url 能正常播
        video_url = data.get("hls_url", "")
        if not video_url:
            raise ParseError("empty", "央视网没有给播放地址")
        return VideoInfo(
            video_url=video_url,
            cover_url=data.get("image", ""),
            title=data.get("title", ""),
            author=VideoAuthor(name=data.get("play_channel", "")),
        )

    @staticmethod
    def _extract_guid_from_html(html: str) -> str:
        match = _GUID.search(html)
        if match and match.group(1):
            return match.group(1)
        raise ParseError("parse", "页面中未找到视频 GUID")
