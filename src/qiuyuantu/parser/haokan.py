import re

from ..utils import get_val_from_url_by_query_key
from .baidu import video_formats
from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, number


class HaoKan(BaseParser):
    """好看视频"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "vid"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[0-9]{1,20}", video_id):
            raise ParseError("unsupported", "好看视频链接里没有有效的作品编号")
        body = await self.get_json(f"https://haokan.baidu.com/v?_format=json&vid={video_id}")
        if body["errno"] != 0:
            raise ValueError(body["error"])  # 平台给的原因，交给 classify 归类
        meta = body["data"]["apiData"]["curVideoMeta"]
        if meta.get("id") and str(meta["id"]) != video_id:
            raise ParseError("parse", "好看视频返回的作品编号与链接不一致")
        # 部分作品（如转载自其他来源）没有 mth，作者只在顶层的 source_name / avatar 里
        user = meta.get("mth") or {}
        info = VideoInfo(
            video_url="",
            cover_url=meta["poster"],
            title=meta["title"],
            author=VideoAuthor(
                uid=str(user.get("mthid") or meta.get("siteAccountId") or ""),
                name=user.get("author_name") or meta.get("source_name") or "",
                avatar=user.get("author_photo") or meta.get("avatar") or "",
            ),
            duration=number(meta.get("duration")),
        )
        return apply_video_formats(info, await video_formats(meta, "https://haokan.baidu.com/"))
