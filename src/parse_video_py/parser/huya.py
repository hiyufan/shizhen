import re

from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError

_VIDEO_ID = re.compile(r"/(\d+)\.html")


class HuYa(BaseParser):
    """虎牙"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        match = _VIDEO_ID.search(share_url)
        if not match:
            raise ParseError("unsupported", "链接里没有虎牙视频 ID")
        return await self.parse_video_id(match.group(1))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.get_json(
            f"https://liveapi.huya.com/moment/getMomentContent?videoId={video_id}",
            headers={"User-Agent": self.ua("windows"), "Referer": "https://v.huya.com/"},
        )
        video = body["data"]["moment"]["videoInfo"]
        if video["uid"] == 0:
            raise ParseError("deleted", "虎牙返回空视频")
        return VideoInfo(
            video_url=video["definitions"][0]["url"],
            cover_url=video["videoCover"],
            title=video["videoTitle"],
            author=VideoAuthor(uid=str(video["uid"]), name=video["actorNick"], avatar=video["actorAvatarUrl"]),
        )
