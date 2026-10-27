"""视频号匿名分享：官方预览核对身份，公益接口补充实际可用的视频。"""

import os
import re
from urllib.parse import parse_qs, urlparse

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .channels_public import resolver, same_post
from .errors import ParseError

API = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
_SHORT = re.compile(r"[A-Za-z0-9_-]{3,100}\Z")


def is_share_page(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname in {"weixin.qq.com", "mp.weixin.qq.com"} and parsed.path.startswith("/sph/")) or (
        parsed.hostname == "channels.weixin.qq.com" and parsed.path == "/finder-preview/pages/sph"
    )


def share_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not is_share_page(url):
        raise ParseError("unsupported", "请复制视频号的完整 sph 分享链接")
    ident = (
        (parse_qs(parsed.query).get("id") or [""])[0]
        if parsed.hostname == "channels.weixin.qq.com"
        else parsed.path[5:]
    )
    if not _SHORT.fullmatch(ident):
        raise ParseError("unsupported", "视频号分享链接无效")
    return ident


def _media(url: str) -> str:
    if not url:
        return ""
    if not isinstance(url, str):
        raise ParseError("parse", "视频号返回的媒体地址不可用")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    allowed = any(host == name or host.endswith("." + name) for name in ("video.qq.com", "qpic.cn"))
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.port:
        raise ParseError("parse", "视频号返回的媒体地址不可用")
    return url


def _videos(feed: dict) -> list[FormatInfo]:
    variants = []
    for key, label in (("h264VideoInfo", "H.264"), ("h265VideoInfo", "H.265")):
        url = _media((feed.get(key) or {}).get("videoUrl") or "")
        if url:
            variants.append(FormatInfo(label=label, url=url, codec=label))
    if url := _media(feed.get("videoUrl") or ""):
        variants.append(FormatInfo(label="MP4", url=url))
    return list({variant.url: variant for variant in variants}.values())


def _public_videos(data: dict) -> list[FormatInfo]:
    primary = _media(data.get("url") or "")
    if not primary or data.get("type") != "video":
        return []
    variants = [FormatInfo(label="MP4", url=primary)]
    for row in data.get("video_backup") or []:
        if not isinstance(row, dict) or row.get("format") != "mp4":
            continue
        url = _media(row.get("url") or "")
        if url and url != primary:
            codec = {"h264": "H.264", "h265": "H.265"}.get(row.get("codec"), "MP4")
            variants.append(FormatInfo(label=codec, codec=codec, url=url))
    # 实测接口标称 1080p 的文件是 1024×576，不能把标签当作实测尺寸展示。
    return list({variant.url: variant for variant in variants}.values())


class WeChatChannels(BaseParser):
    def get_default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.ua("windows"),
            "Referer": "https://channels.weixin.qq.com/",
            "Origin": "https://channels.weixin.qq.com",
        }

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(share_id(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not _SHORT.fullmatch(video_id):
            raise ParseError("unsupported", "视频号分享链接无效")
        # 与官方公开 sph 网页相同的匿名请求；不借用元宝或微信会话。
        response = await self.post_json(API, json={"baseReq": {"generalToken": ""}, "shortUri": video_id})
        data = response.get("data") or {}
        if response.get("errCode") != 0 or (data.get("errMsg") or {}).get("type", 0) != 0:
            raise ParseError("restricted", "这条视频号内容当前没有可用的公开预览")
        feed = data.get("feedInfo") or {}
        cover = _media(feed.get("coverUrl") or "")
        images = [ImgInfo(url=_media(row["url"])) for row in feed.get("picInfo") or [] if row.get("url")]
        videos, notice = await self._available_videos(video_id, data)
        if not videos and not images and not cover:
            raise ParseError("empty", "视频号未返回可下载的公开素材")
        cover_only = not videos and not images
        return VideoInfo(
            video_url=videos[0].url if videos else "",
            cover_url=cover,
            images=images or ([ImgInfo(url=cover)] if cover else []),
            title=feed.get("description") or "视频号",
            author=VideoAuthor(name=(data.get("authorInfo") or {}).get("nickname") or ""),
            formats=videos[1:],
            page_url="https://weixin.qq.com/sph/" + video_id,
            video_headers={"Referer": "https://channels.weixin.qq.com/"},
            notice="当前公开预览只提供封面，未返回可下载视频。" if cover_only else notice,
        )

    async def _available_videos(self, ident: str, data: dict) -> tuple[list[FormatInfo], str]:
        videos = _videos(data.get("feedInfo") or {})
        if videos or os.getenv("PARSE_VIDEO_CHANNELS_PUBLIC_FALLBACK", "1") == "0":
            return videos, ""
        from ..progress import emit

        emit("fallback", fallback=True)
        reply = await resolver.resolve(self, "https://weixin.qq.com/sph/" + ident)
        if not same_post(data, reply):
            return [], ""
        try:
            videos = _public_videos(reply["data"])
        except (ValueError, TypeError, ParseError):
            return [], ""
        return videos, "视频通过 BugPk 公益接口匿名解析，封面与作者来自官方公开预览。" if videos else ""
