from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo


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
        member = post["member"]
        return VideoInfo(
            video_url=post["videos"][str(post["imgs"][0]["id"])]["url"],
            cover_url="",
            title=post["content"],
            author=VideoAuthor(
                uid=str(member["id"]), name=member["name"], avatar=member["avatar_urls"]["origin"]["urls"][0]
            ),
        )
