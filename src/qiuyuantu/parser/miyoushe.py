"""米游社单篇公开帖子：只取正文图集，不把头像、封面或推荐帖混进结果。"""

import re
from urllib.parse import urlparse

from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

API = "https://bbs-api.miyoushe.com/post/wapi/getPostFull"
COMMUNITIES = {"bh3": 1, "ys": 2, "bh2": 3, "wd": 4, "dby": 5, "sr": 6, "zzz": 8}
IMAGE_HOSTS = {"upload-bbs.miyoushe.com", "upload-bbs.mihoyo.com"}


def post_ref(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    path = parsed.fragment if parsed.fragment.startswith("/") else parsed.path
    if path.startswith("/article/"):
        path = parsed.path.rstrip("/") + path
    match = re.fullmatch(r"/([a-z0-9]+)/article/([1-9][0-9]{0,19})/?", path.split("?")[0])
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"www.miyoushe.com", "m.miyoushe.com"}
        or not match
        or match[1] not in COMMUNITIES
    ):
        raise ParseError("unsupported", "请复制米游社单篇帖子的完整链接")
    return match[2], COMMUNITIES[match[1]]


def _images(post: dict) -> list[ImgInfo]:
    urls = (post.get("post") or {}).get("images") or [row.get("url") for row in post.get("image_list") or []]
    result = []
    for url in dict.fromkeys(urls):
        if not url:
            continue
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in IMAGE_HOSTS
            or parsed.username
            or parsed.port
        ):
            raise ParseError("parse", "米游社返回的图片地址不可用")
        # 正文 images 已提供原图；不要去掉查询参数，以免破坏平台签名。
        result.append(ImgInfo(url=url))
    return result


class MiYouShe(BaseParser):
    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua("windows"), "Referer": "https://www.miyoushe.com/"}

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        ident, game = post_ref(share_url)
        return await self._post(ident, game)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[1-9][0-9]{0,19}", video_id):
            raise ParseError("unsupported", "米游社帖子编号无效")
        return await self._post(video_id, 2)

    async def _post(self, ident: str, game: int) -> VideoInfo:
        data = await self.get_json(API, params={"gids": game, "post_id": ident, "read": 1})
        if data.get("retcode") != 0:
            raise ParseError("restricted", "米游社暂未提供这篇帖子的公开数据")
        post = (data.get("data") or {}).get("post") or {}
        core = post.get("post") or {}
        if core.get("is_deleted") or not core:
            raise ParseError("deleted")
        if str(core.get("post_id")) != ident:
            raise ParseError("parse", "米游社返回的帖子编号不匹配")
        images = _images(post)
        if not images:
            raise ParseError("empty", "这篇米游社帖子没有公开的正文图片")
        user = post.get("user") or {}
        community = next((slug for slug, gid in COMMUNITIES.items() if gid == core.get("game_id")), "ys")
        return VideoInfo(
            video_url="",
            cover_url=images[0].url,
            images=images,
            title=core.get("subject") or "米游社",
            author=VideoAuthor(uid=str(user.get("uid") or ""), name=user.get("nickname") or ""),
            page_url=f"https://www.miyoushe.com/{community}/article/{ident}",
            video_headers={"Referer": "https://www.miyoushe.com/"},
        )
