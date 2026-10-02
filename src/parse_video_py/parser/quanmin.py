from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo


class QuanMin(BaseParser):
    """度小视（原全民小视频）"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "vid"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.get_json(
            "https://quanmin.hao222.com/wise/growth/api/sv/immerse"
            f"?source=share-h5&pd=qm_share_mvideo&_format=json&vid={video_id}"
        )
        if body["errno"] != 0:
            raise ValueError(body["error"])  # 平台给的原因，交给 classify 归类
        data = body["data"]
        meta = data["meta"]
        if meta["statusText"]:
            raise ValueError(meta["statusText"])  # 作品状态（删了 / 审核中）
        author = data["author"]
        return VideoInfo(
            video_url=meta["video_info"]["clarityUrl"][1]["url"],
            cover_url=meta["image"],
            title=meta["title"] or data["shareInfo"]["title"],
            author=VideoAuthor(uid=author["id"], name=author["name"], avatar=author["icon"]),
        )
