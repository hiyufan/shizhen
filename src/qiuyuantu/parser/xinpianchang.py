import asyncio
import json
from urllib.parse import quote, urlencode

from parsel import Selector

from ..utils import gather_or_cancel
from . import manifests
from .base import BaseParser, FormatInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, direct_format


class XinPianChang(BaseParser):
    """新片场"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        detail, media = await self.video_resources(share_url)
        user = detail["author"]["userinfo"]
        info = VideoInfo(
            video_url="",
            cover_url=detail["cover"],
            title=detail["title"],
            author=VideoAuthor(uid=str(user["id"]), name=user["username"], avatar=user["avatar"]),
            video_headers={"Referer": share_url, "Range": "bytes=0-"},
        )
        formats = [direct_format(row["url"], row) for row in media.get("progressive") or [] if row.get("url")]
        formats.extend(await self.stream_formats(media, share_url))
        if not formats:
            raise ParseError("empty", "新片场没有返回可下载的视频")
        return apply_video_formats(info, formats)

    async def video_resources(self, share_url: str) -> tuple[dict, dict]:
        headers = {
            "User-Agent": self.ua("windows"),
            "Upgrade-Insecure-Requests": "1",
            "Referer": "https://www.xinpianchang.com/",
        }
        html = await self.get_text(share_url, headers=headers, follow_redirects=True)
        next_data = Selector(html).css("script#__NEXT_DATA__::text").get()
        if not next_data:
            raise ParseError("parse", "页面里没找到作品数据")
        detail = json.loads(next_data)["props"]["pageProps"]["detail"]
        # 页面里只有 appKey 和 media_id，mp4 地址要另调一个接口
        video = detail.get("video") or {}
        media_id = video.get("vid") or detail.get("media_id")
        if not media_id or not video.get("appKey"):
            raise ParseError("parse", "新片场没有提供视频编号")
        query = urlencode({"appKey": video["appKey"], "extend": "userInfo,userStatus"})
        media = await self.get_json(
            f"https://mod-api.xinpianchang.com/mod/api/v2/media/{quote(str(media_id), safe='')}?{query}",
            headers=headers,
            follow_redirects=True,
        )
        return detail, (media.get("data") or {}).get("resource") or {}

    async def stream_formats(self, resources: dict, page_url: str) -> list[FormatInfo]:
        async def formats(kind: str, url: str) -> list[FormatInfo]:
            text = await self.get_text(
                url,
                headers={"User-Agent": self.ua("windows"), "Referer": page_url, "Range": "bytes=0-"},
                follow_redirects=True,
                timeout=10,
            )
            rows = await asyncio.to_thread(manifests.tracks, text, url, kind)
            return [manifests.download_format(row, f"xpc:{kind}") for row in rows]

        groups = await gather_or_cancel(
            *(
                formats(kind, resource["url"])
                for kind in ("hls", "dash")
                if isinstance(resource := resources.get(kind), dict) and resource.get("url")
            )
        )
        return [fmt for group in groups for fmt in group]

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise NotImplementedError("新片场暂不支持直接解析视频ID")
