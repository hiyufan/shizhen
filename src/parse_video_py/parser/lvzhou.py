import re

from parsel import Selector

from .base import BaseParser, VideoAuthor, VideoInfo

_COVER = re.compile(r"background-image:url\((.*)\)")


class LvZhou(BaseParser):
    """绿洲"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        sel = Selector(await self.get_text(share_url))
        cover = _COVER.search(sel.css("div.video-cover::attr(style)").get(default=""))
        return VideoInfo(
            video_url=sel.css("video::attr(src)").get(),
            cover_url=cover.group(1) if cover else "",
            title=sel.css("div.status-title::text").get(),
            author=VideoAuthor(
                name=sel.css("div.nickname::text").get(), avatar=sel.css("a.avatar img::attr(src)").get()
            ),
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(f"https://m.oasis.weibo.cn/v1/h5/share?sid={video_id}")
