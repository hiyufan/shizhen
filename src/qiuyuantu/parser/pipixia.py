import re
from urllib.parse import urljoin, urlparse

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, direct_format, number, verify_direct_formats

_API = (
    "https://api.pipix.com/bds/cell/cell_comment/?offset=0&cell_type=1&api_version=1&cell_id={id}"
    "&ac=wifi&channel=huawei_1319_64&aid=1319&app_name=super"
)


def _first_url(media: dict) -> str:
    return next((row["url"] for row in media.get("url_list") or [] if isinstance(row, dict) and row.get("url")), "")


def _video_id(video: dict) -> str:
    return video.get("video_id") or next(
        (
            value.get("uri")
            for key, value in video.items()
            if key.startswith("video_")
            and "cover" not in key
            and "image" not in key
            and isinstance(value, dict)
            and re.fullmatch(r"v[A-Za-z0-9]{10,127}", str(value.get("uri") or ""))
        ),
        "",
    )


def _videos(item: dict) -> list[dict]:
    """同作者的评论也可能是另一段视频；只有相同媒体编号才能作为去水印替代档。"""
    primary = item.get("video") or {}
    ident = _video_id(primary)
    matching = []
    for comment in item.get("comments") or []:
        own = comment.get("item") or {}
        video = own.get("video") or {}
        same_author = str((own.get("author") or {}).get("id")) == str(item["author"]["id"])
        if ident and same_author and _video_id(video) == ident:
            matching.append(video)
    return [*matching, primary]


async def _formats(item: dict) -> list[FormatInfo]:
    candidates = [
        direct_format(url, value)
        for video in _videos(item)
        for key, value in video.items()
        if key.startswith("video_")
        and "cover" not in key
        and "image" not in key
        and isinstance(value, dict)
        and (url := _first_url(value))
    ]
    return await verify_direct_formats(candidates, {"Referer": "https://h5.pipix.com/"}, all_tracks=True)


class PiPiXia(BaseParser):
    """皮皮虾"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        target = share_url
        if not re.fullmatch(r"/item/[0-9]+/?", urlparse(target).path):
            location = await self.redirect_target(share_url)
            if not location:
                raise ParseError("deleted", "皮皮虾短链没有跳转到作品页")
            target = urljoin(share_url, location)
        return await self.parse_video_id(urlparse(target).path.rstrip("/").split("/")[-1])

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[0-9]{1,20}", video_id):
            raise ParseError("unsupported", "皮皮虾链接里没有有效的作品编号")
        body = await self.get_json(_API.format(id=video_id))
        if body["status_code"] != 0:
            raise ValueError(f"获取作品信息失败:prompt={body['prompt']}")
        comments = (body.get("data") or {}).get("cell_comments") or []
        if not comments:
            raise ParseError("restricted", "皮皮虾没有返回作品数据")
        item = comments[0]["comment_info"]["item"]
        ident = str(item.get("item_id_str") or item.get("item_id") or "")
        if ident and ident != video_id:
            raise ParseError("parse", "皮皮虾返回的作品编号与链接不一致")
        note = item.get("note") or {}
        author = item["author"]
        info = VideoInfo(
            video_url="",
            cover_url=_first_url(item["cover"]),
            title=item["content"],
            images=[ImgInfo(url=_first_url(img)) for img in note.get("multi_image") or []],
            author=VideoAuthor(
                uid=str(author["id"]), name=author["name"], avatar=author["avatar"]["download_list"][0]["url"]
            ),
            duration=number((item.get("video") or {}).get("duration")),
        )
        return apply_video_formats(info, await _formats(item))
