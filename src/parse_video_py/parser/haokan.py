from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo


class HaoKan(BaseParser):
    """好看视频"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "vid"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.get_json(f"https://haokan.baidu.com/v?_format=json&vid={video_id}")
        if body["errno"] != 0:
            raise ValueError(body["error"])  # 平台给的原因，交给 classify 归类
        meta = body["data"]["apiData"]["curVideoMeta"]
        user = meta["mth"]
        return VideoInfo(
            video_url=meta["playurl"],
            cover_url=meta["poster"],
            title=meta["title"],
            author=VideoAuthor(uid=user["mthid"], name=user["author_name"], avatar=user["author_photo"]),
        )
