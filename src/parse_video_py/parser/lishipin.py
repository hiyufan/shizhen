import time
from urllib.parse import urlparse

from .base import BaseParser, VideoInfo
from .errors import ParseError


class LiShiPin(BaseParser):
    """梨视频"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        video_id = urlparse(share_url).path.replace("/detail_", "")
        if not video_id:
            raise ParseError("unsupported", "链接里没有梨视频的作品 ID")
        return await self.parse_video_id(video_id)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.get_json(
            f"https://www.pearvideo.com/videoStatus.jsp?contId={video_id}&mrd={int(time.time())}",
            headers={"Referer": f"https://www.pearvideo.com/detail_{video_id}", "User-Agent": self.ua("windows")},
        )
        info = body["videoInfo"]
        # 接口给的地址里用时间戳顶替了作品 ID，换回来才能播
        video_url = info["videos"]["srcUrl"].replace(body["systemTime"], f"cont-{video_id}")
        return VideoInfo(video_url=video_url, cover_url=info["video_image"])
