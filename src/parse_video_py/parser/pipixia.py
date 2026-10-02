from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

_API = (
    "https://api.pipix.com/bds/cell/cell_comment/?offset=0&cell_type=1&api_version=1&cell_id={id}"
    "&ac=wifi&channel=huawei_1319_64&aid=1319&app_name=super"
)


def _first_url(media: dict) -> str:
    return media["url_list"][0]["url"]


def _video_url(item: dict) -> str:
    """作品自带的视频可能有水印；作者自己在评论区发的同一段视频没有，有就用那个。"""
    author_id = item["author"]["id"]
    for comment in item.get("comments") or []:
        own = comment["item"]
        if own["author"]["id"] == author_id and (url := _first_url(own["video"]["video_high"])):
            return url
    return _first_url(item["video"]["video_high"])


class PiPiXia(BaseParser):
    """皮皮虾"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        location = await self.redirect_target(share_url)
        if not location:
            raise ParseError("deleted", "皮皮虾短链没有跳转到作品页")
        return await self.parse_video_id(location.split("?")[0].split("/")[-1])

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.get_json(_API.format(id=video_id))
        if body["status_code"] != 0:
            raise ValueError(f"获取作品信息失败:prompt={body['prompt']}")
        item = body["data"]["cell_comments"][0]["comment_info"]["item"]
        note = item.get("note") or {}
        author = item["author"]
        return VideoInfo(
            video_url=_video_url(item) if item.get("video") else "",
            cover_url=_first_url(item["cover"]),
            title=item["content"],
            images=[ImgInfo(url=_first_url(img)) for img in note.get("multi_image") or []],
            author=VideoAuthor(
                uid=str(author["id"]), name=author["name"], avatar=author["avatar"]["download_list"][0]["url"]
            ),
        )
