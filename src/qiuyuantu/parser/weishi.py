from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo


class WeiShi(BaseParser):
    """微视"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "id"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.get_json(f"https://h5.weishi.qq.com/webapp/json/weishi/WSH5GetPlayPage?feedid={video_id}")
        if body["ret"] != 0:
            raise ValueError(body["msg"])  # 平台给的原因，交给 classify 归类
        if body["data"]["errmsg"]:
            raise ValueError(body["data"]["errmsg"])  # 作品状态
        feed = body["data"]["feeds"][0]
        poster = feed["poster"]
        return VideoInfo(
            video_url=feed["video_url"],
            cover_url=feed["images"][0]["url"],
            title=feed["feed_desc_withat"],
            author=VideoAuthor(uid=feed["id"], name=poster["nick"], avatar=poster["avatar"]),
        )
