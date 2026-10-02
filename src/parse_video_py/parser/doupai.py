from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo


class DouPai(BaseParser):
    """逗拍"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "id"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        data = (await self.get_json(f"https://v2.doupai.cc/topic/{video_id}.json"))["data"]
        user = data["userId"]
        return VideoInfo(
            video_url=data["videoUrl"],
            cover_url=data["imageUrl"],
            title=data["name"],
            author=VideoAuthor(uid=user["id"], name=user["name"], avatar=user["avatar"]),
        )
