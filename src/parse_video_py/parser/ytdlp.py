"""
YouTube / TikTok / Instagram 以及其它没有专用解析器的站点, 统一交给 yt-dlp。

直链只挑「音视频合一」的 mp4 (浏览器能直接播放、代理能直接流式转发);
更高的清晰度需要服务端用 ffmpeg 合并, 放进 formats 里让前端按需下载。
"""

import asyncio
from typing import Any, Dict, List, Optional

from ..convert import config as convert_config
from ..convert.ffmpeg import ffmpeg_dir
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
    def _extract(url: str) -> Dict[str, Any]:
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

    @classmethod
    def _to_video_info(cls, info: Dict[str, Any], share_url: str) -> VideoInfo:
        formats: List[Dict[str, Any]] = [f for f in (info.get("formats") or []) if f.get("url")]

        def is_video(f: Dict[str, Any]) -> bool:
            return f.get("vcodec") not in (None, "none")

        def is_audio(f: Dict[str, Any]) -> bool:
            return f.get("acodec") not in (None, "none")

        def is_http(f: Dict[str, Any]) -> bool:
            return (f.get("protocol") or "https").startswith("http") and not f.get("manifest_url")

        progressive = [f for f in formats if is_video(f) and is_audio(f) and is_http(f)]
        progressive.sort(key=lambda f: ((f.get("ext") == "mp4"), f.get("height") or 0, f.get("tbr") or 0))
        best_direct: Optional[Dict[str, Any]] = progressive[-1] if progressive else None

        video_url = ""
        headers: Dict[str, str] = {}
        width = height = 0
        if best_direct:
            video_url = best_direct["url"]
            headers = dict(best_direct.get("http_headers") or {})
            width, height = best_direct.get("width") or 0, best_direct.get("height") or 0
        elif info.get("url") and info.get("ext") in ("mp4", "mov", "webm"):
            video_url = info["url"]
            headers = dict(info.get("http_headers") or {})
            width, height = info.get("width") or 0, info.get("height") or 0

        # 更高清晰度: 视频轨最大高度 > 直链高度的, 给出合并选项
        merged: List[FormatInfo] = []
        video_heights = sorted({f.get("height") or 0 for f in formats if is_video(f)}, reverse=True)
        top = video_heights[0] if video_heights else 0
        if top and top > height:
            seen = set()
            for h in _HEIGHT_LADDER:
                if h > top or h <= height or h in seen:
                    continue
                if not any((f.get("height") or 0) >= h for f in formats if is_video(f)):
                    continue
                seen.add(h)
                approx = max(
                    ((f.get("filesize") or f.get("filesize_approx") or 0) for f in formats
                     if is_video(f) and (f.get("height") or 0) == h),
                    default=0,
                )
                merged.append(FormatInfo(
                    label=f"{h}p",
                    format_spec=f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]",
                    ext="mp4",
                    height=h,
                    filesize=int(approx),
                ))
        if any(is_audio(f) and not is_video(f) for f in formats):
            merged.append(FormatInfo(label="仅音频", format_spec="ba[ext=m4a]/ba", ext="m4a", height=0))

        # 图片: yt-dlp 把纯图片帖当 thumbnails 或 formats(ext=jpg) 返回
        images: List[ImgInfo] = []
        for f in formats:
            if f.get("ext") in ("jpg", "jpeg", "png", "webp") and not is_video(f):
                images.append(ImgInfo(url=f["url"]))

        cover = info.get("thumbnail") or ""
        if not cover and info.get("thumbnails"):
            cover = info["thumbnails"][-1].get("url", "")

        extractor = (info.get("extractor_key") or info.get("extractor") or "ytdlp").lower()
        for key in ("youtube", "tiktok", "instagram", "vimeo", "facebook", "twitch", "reddit", "pinterest"):
            if key in extractor:
                extractor = key
                break

        return VideoInfo(
            video_url=video_url,
            cover_url=cover,
            title=info.get("title") or info.get("description") or "",
            images=images,
            author=VideoAuthor(
                uid=str(info.get("uploader_id") or info.get("channel_id") or ""),
                name=info.get("uploader") or info.get("channel") or info.get("creator") or "",
                avatar="",
            ),
            source=extractor,
            page_url=info.get("webpage_url") or share_url,
            duration=float(info.get("duration") or 0),
            width=width,
            height=height,
            formats=merged,
            video_headers=headers,
        )
