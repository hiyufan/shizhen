"""
YouTube / TikTok / Instagram 以及其它没有专用解析器的站点, 统一交给 yt-dlp。

直链只挑「音视频合一」的 mp4 (浏览器能直接播放、代理能直接流式转发);
更高的清晰度需要服务端用 ffmpeg 合并, 放进 formats 里让前端按需下载。
"""

import asyncio
from typing import Any

from ..convert import config as convert_config
from ..convert.ffmpeg import ffmpeg_dir
from ..utils import proxy_for
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo

_HEIGHT_LADDER = (2160, 1440, 1080, 720, 480, 360)


class YtDlp(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        info = await asyncio.to_thread(self._extract, share_url)
        return self._to_video_info(info, share_url)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        # 没有统一的 id 规则, 只接受完整链接
        return await self.parse_share_url(video_id)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _extract(url: str) -> dict[str, Any]:
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 25,
            "ffmpeg_location": ffmpeg_dir(),
            **convert_config.ytdlp_cookie_opts(),
        }
        if proxy := proxy_for():
            opts["proxy"] = proxy
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        # 播放列表 / 多图帖只取第一条
        while info and info.get("_type") in ("playlist", "multi_video") and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if not entries:
                break
            info = entries[0]
        if not info:
            raise ValueError("yt-dlp 没有解析出内容")
        return info

    @staticmethod
    def _to_video_info(info: dict[str, Any], share_url: str) -> VideoInfo:
        formats = [f for f in (info.get("formats") or []) if f.get("url")]
        video_url, headers, width, height = _direct_stream(info, formats)
        return VideoInfo(
            video_url=video_url,
            cover_url=_cover(info),
            title=info.get("title") or info.get("description") or "",
            images=[ImgInfo(url=f["url"]) for f in formats if f.get("ext") in _IMAGE_EXTS and not _is_video(f)],
            author=VideoAuthor(
                uid=str(info.get("uploader_id") or info.get("channel_id") or ""),
                name=info.get("uploader") or info.get("channel") or info.get("creator") or "",
                avatar="",
            ),
            source=_site_name(info),
            page_url=info.get("webpage_url") or share_url,
            duration=float(info.get("duration") or 0),
            width=width,
            height=height,
            formats=_merge_options(formats, height),
            video_headers=headers,
        )


# 纯图片帖：yt-dlp 把图当 thumbnails 或 formats(ext=jpg) 返回
_IMAGE_EXTS = ("jpg", "jpeg", "png", "webp")
_KNOWN_SITES = ("youtube", "tiktok", "instagram", "vimeo", "facebook", "twitch", "reddit", "pinterest")


def _is_video(f: dict[str, Any]) -> bool:
    return f.get("vcodec") not in (None, "none")


def _is_audio(f: dict[str, Any]) -> bool:
    return f.get("acodec") not in (None, "none")


def _is_http(f: dict[str, Any]) -> bool:
    return (f.get("protocol") or "https").startswith("http") and not f.get("manifest_url")


def _direct_stream(info: dict[str, Any], formats: list[dict[str, Any]]) -> tuple[str, dict[str, str], int, int]:
    """浏览器能直接播的那一路：音视频合一的 http 直链，mp4 优先、越清晰越好。
    返回 (地址, 要带的请求头, 宽, 高)；没有就是空地址。"""
    progressive = [f for f in formats if _is_video(f) and _is_audio(f) and _is_http(f)]
    best = max(
        reversed(progressive),  # 一样好的取后面那个（yt-dlp 按从差到好排）
        key=lambda f: (f.get("ext") == "mp4", f.get("height") or 0, f.get("tbr") or 0),
        default=None,
    )
    if best is None and info.get("url") and info.get("ext") in ("mp4", "mov", "webm"):
        best = info
    if best is None:
        return "", {}, 0, 0
    return best["url"], dict(best.get("http_headers") or {}), best.get("width") or 0, best.get("height") or 0


def _merge_options(formats: list[dict[str, Any]], direct_height: int) -> list[FormatInfo]:
    """比直链更清晰的档位（要服务端合并音视频），以及「仅音频」。"""
    videos = [f for f in formats if _is_video(f)]
    top = max((f.get("height") or 0 for f in videos), default=0)
    options = []
    for h in _HEIGHT_LADDER:
        if not (direct_height < h <= top):
            continue
        same_height = [f for f in videos if (f.get("height") or 0) == h]
        approx = max((f.get("filesize") or f.get("filesize_approx") or 0 for f in same_height), default=0)
        options.append(
            FormatInfo(
                label=f"{h}p",
                format_spec=f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]",
                ext="mp4",
                height=h,
                filesize=int(approx),
            )
        )
    if any(_is_audio(f) and not _is_video(f) for f in formats):
        options.append(FormatInfo(label="仅音频", format_spec="ba[ext=m4a]/ba", ext="m4a", height=0))
    return options


def _cover(info: dict[str, Any]) -> str:
    if cover := info.get("thumbnail"):
        return cover
    thumbnails = info.get("thumbnails") or []
    return thumbnails[-1].get("url", "") if thumbnails else ""


def _site_name(info: dict[str, Any]) -> str:
    extractor = (info.get("extractor_key") or info.get("extractor") or "ytdlp").lower()
    return next((key for key in _KNOWN_SITES if key in extractor), extractor)
