from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError
from .ippzone import first_video, post_images, video_formats
from .quality import apply_video_formats, number


class ZuiYou(BaseParser):
    """最右"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "pid"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.post_json(
            "https://share.xiaochuankeji.cn/planck/share/post/detail_h5",
            json={"h_av": "5.2.13.011", "pid": int(video_id)},
            follow_redirects=True,
        )
        post = body["data"]["post"]
        if post.get("id") and str(post["id"]) != video_id:
            raise ParseError("parse", "最右返回的作品编号与链接不一致")
        member = post["member"]
        video = first_video(post)
        images = post_images(post)
        info = VideoInfo(
            video_url="",
            cover_url=images[0].url if images else "",
            images=images,
            title=post["content"],
            author=VideoAuthor(
                uid=str(member["id"]), name=member["name"], avatar=member["avatar_urls"]["origin"]["urls"][0]
            ),
            duration=number(video.get("dur")),
        )
        return apply_video_formats(info, await video_formats(video, "https://share.xiaochuankeji.cn/"))
