from urllib.parse import urlparse

from .base import BaseParser, VideoInfo
from .errors import ParseError

_API = "https://share.ippzone.com/ppapi/share/fetch_content"


class PiPiGaoXiao(BaseParser):
    """皮皮搞笑"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        video_id = urlparse(share_url).path.replace("/pp/post/", "")
        if not video_id:
            raise ParseError("unsupported", "链接里没有皮皮搞笑的帖子 ID")
        return await self.parse_video_id(video_id)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        headers = {"Referer": _API, "Content-Type": "text/plain;charset=UTF-8", "User-Agent": self.ua("windows")}
        # pid 要是数字，直接拼 JSON 字符串，不用 json.dumps（它会给字符串 ID 加引号）
        content = '{"pid":' + video_id + ',"type":"post","mid":null}'
        body = await self.post_json(_API, headers=headers, content=content)
        if "msg" in body:
            raise ValueError(body["msg"])  # 平台给的原因，交给 classify 归类
        post = body["data"]["post"]
        img_id = post["imgs"][0]["id"]
        return VideoInfo(
            video_url=post["videos"][str(img_id)]["url"],
            cover_url=f"https://file.ippzone.com/img/view/id/{img_id}",
            title=post["content"],
        )
