import re

from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, direct_format

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
        info = VideoInfo(
            video_url="",
            cover_url=video["videoCover"],
            title=video["videoTitle"],
            author=VideoAuthor(uid=str(video["uid"]), name=video["actorNick"], avatar=video["actorAvatarUrl"]),
        )
        formats = [
            direct_format(row.get("url") or row["m3u8"], row, label=row.get("defName") or "")
            for row in video.get("definitions") or []
            if row.get("url") or row.get("m3u8")
        ]
        if not formats:
            raise ParseError("empty", "虎牙没有返回可直接下载的视频")
        return apply_video_formats(info, formats)
