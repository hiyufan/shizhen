from urllib.parse import urlparse

from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError


class SixRoom(BaseParser):
    """六间房"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        if "watchMini.php?vid=" in share_url:
            video_id = get_val_from_url_by_query_key(share_url, "vid")
        else:
            # m.6.cn/v/<id>：路径最后一段。只有域名、没有路径的链接没有作品 ID
            video_id = urlparse(share_url).path.strip("/").split("/")[-1]
        if not video_id:
            raise ParseError("unsupported", "链接里没有六间房的作品 ID")
        return await self.parse_video_id(video_id)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.get_json(
            "https://v.6.cn/coop/mobile/index.php?padapi=minivideo-watchVideo.php&av=3.0"
            f"&encpass=&logiuid=&isnew=1&from=0&vid={video_id}",
            headers={"Referer": f"https://m.6.cn/v/{video_id}", "User-Agent": self.ua("iOS")},
            follow_redirects=True,
        )
        content = body["content"]
        return VideoInfo(
            video_url=content["playurl"],
            cover_url=content["picurl"],
            title=content["title"],
            author=VideoAuthor(name=content["alias"], avatar=content["picuser"]),
        )
