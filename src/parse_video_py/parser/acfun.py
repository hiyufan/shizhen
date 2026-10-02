from parsel import Selector

from .base import BaseParser, VideoAuthor, VideoInfo, json_in_html


class AcFun(BaseParser):
    """A站：视频地址是 m3u8"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        html = await self.get_text(share_url, follow_redirects=True)
        video = json_in_html(html, r"var videoInfo =\s(.*?);", "视频信息")
        play = json_in_html(html, r"var playInfo =\s(.*?);", "播放信息")
        sel = Selector(html)
        return VideoInfo(
            video_url=play["streams"][0]["playUrls"][0],
            cover_url=video["cover"],
            title=video["title"],
            author=VideoAuthor(
                uid=sel.css("div.up-info > a.info-item1::attr(href)").get(default="").replace("/upPage/", ""),
                name=sel.css("div.up-info span.up-name::text").get(default=""),
                avatar=sel.css("div.up-info span.up-avatar > img::attr(src)").get(default=""),
            ),
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        # acid，格式：ac36935385
        return await self.parse_share_url(f"https://www.acfun.cn/v/{video_id}")
