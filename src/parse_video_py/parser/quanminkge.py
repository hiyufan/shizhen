from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo, json_in_html


class QuanMinKGe(BaseParser):
    """全民K歌"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "s"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        html = await self.get_text(
            f"https://kg.qq.com/node/play?s={video_id}", headers={"User-Agent": self.ua("windows")}
        )
        detail = json_in_html(html, r"window.__DATA__ = (.*?); </script>", "作品数据")["detail"]
        return VideoInfo(
            video_url=detail["playurl_video"],
            cover_url=detail["cover"],
            title=detail["content"],
            author=VideoAuthor(uid=detail["uid"], name=detail["nick"], avatar=detail["avatar"]),
        )
