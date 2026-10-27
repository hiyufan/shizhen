import re
from urllib.parse import urlparse

from .base import BaseParser, VideoInfo
from .errors import ParseError
from .ippzone import first_video, post_images, video_formats
from .quality import apply_video_formats, number

_API = "https://share.ippzone.com/ppapi/share/fetch_content"


class PiPiGaoXiao(BaseParser):
    """皮皮搞笑"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        match = re.fullmatch(r"/(?:pp|spacey)/post/([0-9]{1,20})/?", urlparse(share_url).path)
        if not match:
            raise ParseError("unsupported", "链接里没有皮皮搞笑的帖子 ID")
        return await self.parse_video_id(match[1])

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[0-9]{1,20}", video_id):
            raise ParseError("unsupported", "皮皮搞笑帖子编号无效")
        headers = {"Referer": _API, "Content-Type": "text/plain;charset=UTF-8", "User-Agent": self.ua("windows")}
        # pid 要是数字，直接拼 JSON 字符串，不用 json.dumps（它会给字符串 ID 加引号）
        content = '{"pid":' + video_id + ',"type":"post","mid":null}'
        body = await self.post_json(_API, headers=headers, content=content)
        if "msg" in body:
            raise ValueError(body["msg"])  # 平台给的原因，交给 classify 归类
        post = body["data"]["post"]
        if post.get("id") and str(post["id"]) != video_id:
            raise ParseError("parse", "皮皮搞笑返回的作品编号与链接不一致")
        images = post_images(post)
        img_id = post["imgs"][0]["id"] if post.get("imgs") else ""
        video = first_video(post)
        info = VideoInfo(
            video_url="",
            cover_url=images[0].url if images else f"https://file.ippzone.com/img/view/id/{img_id}" if img_id else "",
            images=images,
            title=post["content"],
            duration=number(video.get("dur")),
        )
        return apply_video_formats(info, await video_formats(video, "https://h5.pipigx.com/"))
