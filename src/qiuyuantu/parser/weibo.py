import re
from urllib.parse import urlparse

import httpx

from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo, json_in_html
from .errors import ParseError

# 图片按从大到小找第一个有地址的尺寸
_PIC_SIZES = ("large", "original", "bmiddle", "url")


def _largest_pic(pic: dict) -> str:
    return next((pic[size]["url"] for size in _PIC_SIZES if (pic.get(size) or {}).get("url")), "")


def _from_status(status: dict) -> VideoInfo:
    """一条微博（手机接口和网页里内嵌的是同一个结构）-> 图集"""
    user = status.get("user") or {}
    return VideoInfo(
        video_url="",  # 普通微博没有视频；视频微博走 parse_video_id
        cover_url="",
        title=re.sub(r"<[^>]*>", "", status.get("text", "")).strip(),
        images=[ImgInfo(url=url) for pic in status.get("pics") or [] if (url := _largest_pic(pic))],
        author=VideoAuthor(name=user.get("screen_name", ""), avatar=user.get("avatar_large", "")),
    )


class WeiBo(BaseParser):
    """微博：视频（/tv/show/、show?fid=）和普通微博的图集"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        if "show?fid=" in share_url:
            return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "fid"))
        path = urlparse(share_url).path
        if "/tv/show/" in path:
            return await self.parse_video_id(path.replace("/tv/show/", ""))
        # 普通微博：weibo.com/<用户 ID>/<微博 ID>
        parts = path.strip("/").split("/")
        if len(parts) >= 2:
            return await self.parse_post(parts[-1], share_url)
        raise ParseError("unsupported", "不是微博视频或单条微博的链接")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        body = await self.post_json(
            f"https://h5.video.weibo.com/api/component?page=/show/{video_id}",
            headers={
                "Referer": f"https://h5.video.weibo.com/show/{video_id}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.ua("iOS"),
            },
            content='data={"Component_Play_Playinfo":{"oid":"' + video_id + '"}}',
            follow_redirects=True,
        )
        play = body["data"]["Component_Play_Playinfo"]
        # stream_url 码率最低；urls 里第一条码率最高
        video_url = f"https:{next(iter(play['urls'].values()))}" if play["urls"] else play["stream_url"]
        return VideoInfo(
            video_url=video_url,
            cover_url="https:" + play["cover_image"],
            title=play["title"],
            author=VideoAuthor(uid=str(play["user"]["id"]), name=play["author"], avatar="https:" + play["avatar"]),
        )

    async def parse_post(self, post_id: str, page_url: str) -> VideoInfo:
        """普通微博：先走手机接口，拿不到再解析网页里内嵌的数据"""
        try:
            body = await self.get_json(
                f"https://m.weibo.cn/statuses/show?id={post_id}",
                headers={
                    "User-Agent": self.ua("iOS"),
                    "Referer": "https://m.weibo.cn/",
                    "Content-Type": "application/json;charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                },
                follow_redirects=True,
            )
        except (httpx.HTTPError, ValueError):
            body = {}
        if "data" in body:
            return _from_status(body["data"])
        return self._from_page(
            await self.get_text(page_url, headers={"User-Agent": self.ua("iOS")}, follow_redirects=True)
        )

    @staticmethod
    def _from_page(html: str) -> VideoInfo:
        # 页面里是 var $render_data = [{"status": {...}}][0] || {};
        # 以前截出 [...] 之后又拼上 "[0]" 再 json.loads，必然报错，这条兜底从来没成功过
        data = json_in_html(html, r"\$render_data\s*=\s*(\[.*?\])\[0\]", "微博数据")
        if not data or not isinstance(data[0], dict):
            raise ParseError("parse", "微博页面里的数据是空的")
        return _from_status(data[0].get("status") or {})
