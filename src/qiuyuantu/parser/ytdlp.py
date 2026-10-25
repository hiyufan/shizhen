"""
YouTube / TikTok / Instagram 以及其它没有专用解析器的站点, 统一交给 yt-dlp。

直链只挑「音视频合一」的 mp4 (浏览器能直接播放、代理能直接流式转发);
更高的清晰度需要服务端用 ffmpeg 合并, 放进 formats 里让前端按需下载。
"""

import asyncio
import contextlib
from http.cookies import CookieError, SimpleCookie
from typing import Any

from .. import progress
from ..convert import config as convert_config
from ..convert import net
from ..convert.ffmpeg import ffmpeg_dir
from ..utils import fallback_proxy, proxy_for, run_ytdlp, wants_fallback, youtube_proxy
from . import instagram
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo

_HEIGHT_LADDER = (2160, 1440, 1080, 720, 480, 360)


class YtDlp(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        from ..convert import youtube_native

        if youtube_native.cached(share_url):
            progress.emit("cached")
            return await youtube_native.parse(share_url)
        try:
            info = await run_ytdlp(self._extract, share_url)
            return await self._usable_info(info, share_url)
        except Exception as err:
            # 图片帖会报 No video formats found 或 There is no video in this post；
            # 空媒体响应也可能只是 yt-dlp 的接口没拿到，公开页面仍然有原图。
            # 兜底失败不能盖掉原错误（例如本来是限龄，兜底却撞上网络超时）。
            if instagram.post_code(share_url):
                progress.emit("fallback", fallback=True)
                with contextlib.suppress(Exception):
                    if post := await instagram.image_post(share_url):
                        return post
            if wants_fallback(err, share_url):
                progress.emit("fallback", fallback=True)
                if youtube_native.should_try(err, share_url):
                    return await self._race_fallbacks(share_url)
                try:
                    info = await run_ytdlp(self._extract, share_url, fallback_proxy())
                    return await self._usable_info(info, share_url, fallback=True)
                except Exception as fallback_err:
                    err = fallback_err
            if youtube_native.should_try(err, share_url):
                progress.emit("fallback", fallback=True)
                return await self._native_info(share_url, err)
            raise err

    @staticmethod
    async def _native_info(share_url: str, err: Exception) -> VideoInfo:
        from ..convert import youtube_native

        try:
            return await youtube_native.parse(share_url)
        except (asyncio.TimeoutError, youtube_native.Refused):
            raise
        except Exception as native_err:
            # SABR 的泛化 unavailable 不能把原先的风控误报为视频已删除。
            raise err from native_err

    async def _fallback_info(self, share_url: str) -> VideoInfo:
        info = await run_ytdlp(self._extract, share_url, fallback_proxy())
        return await self._usable_info(info, share_url, fallback=True)

    async def _race_fallbacks(self, share_url: str) -> VideoInfo:
        """默认路径失败后并行恢复；只接受可读媒体或完整验证的缓存。"""
        from ..convert import youtube_native

        fallback = asyncio.create_task(self._fallback_info(share_url))
        native = asyncio.create_task(youtube_native.parse(share_url))
        attempts = (fallback, native)
        try:
            for finished in asyncio.as_completed(attempts):
                try:
                    return await finished
                except Exception:
                    if fallback.done() and (err := fallback.exception()) and _definitive(err, share_url):
                        raise err from None
            error = native.exception()
            if isinstance(error, (asyncio.TimeoutError, youtube_native.Refused)):
                raise error
            raise fallback.exception() from error
        finally:
            for attempt in attempts:
                if not attempt.done():
                    attempt.cancel()
            await asyncio.gather(*attempts, return_exceptions=True)

    async def _usable_info(self, info: dict, share_url: str, *, fallback: bool = False) -> VideoInfo:
        from ..convert import youtube_native

        result = self._to_video_info(info, share_url)
        if fallback:
            net.remember_fallback([share_url, result.page_url, result.video_url, *(f.url for f in result.formats)])
        if youtube_native.enabled() and youtube_native.video_id(share_url):
            progress.emit("checking")
            await youtube_native.check_media(result)
        return result

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        # 没有统一的 id 规则, 只接受完整链接
        return await self.parse_share_url(video_id)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _extract(url: str, proxy: str | None = None) -> dict[str, Any]:
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 25,
            "ffmpeg_location": ffmpeg_dir(),
            **convert_config.ytdlp_cookie_opts(),
            **convert_config.ytdlp_runtime_opts(),
        }
        if proxy := proxy or youtube_proxy(url) or proxy_for():
            opts["proxy"] = proxy
        # yt-dlp 自己跟跳转、抓页面里嵌的地址；连内网由 net 里换掉的 getaddrinfo 拦
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


def _definitive(err: Exception, share_url: str) -> bool:
    """备用出口的这个报错能不能直接定论、不用等原生兜底：限龄、要登录这类是视频本身的问题。

    「已删除」不算：走到这一步，默认出口已经报了机器人验证，视频是在的；WARP 偶尔对正常视频
    回 Video unavailable（2026-10-22 iN1N8o3Miv4 被报成「已删除」，紧接着连试 15 次又全成功）。"""
    from ..convert import youtube_native
    from .errors import classify

    return not youtube_native.should_try(err, share_url) and classify(err).reason != "deleted"


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
    return best["url"], _stream_headers(best), best.get("width") or 0, best.get("height") or 0


_KEEP_HEADERS = ("user-agent", "referer", "origin")


def _stream_headers(fmt: dict[str, Any]) -> dict[str, str]:
    """转发这一路要带的请求头：yt-dlp 给的 UA / Referer，加上解析时拿到的 cookie。
    TikTok 的视频地址缺了 cookie（tt_chain_token）一律 403。"""
    headers = {k: v for k, v in (fmt.get("http_headers") or {}).items() if k.lower() in _KEEP_HEADERS}
    # yt-dlp 把 cookie 写成 Set-Cookie 的样子：'a=1; Domain=.x.com; Path=/; Secure; b="x+y=="; ...'，
    # 带特殊字符的值加了引号，原样发出去 TikTok 不认（还是 403），要按 cookie 的规矩解开
    jar = SimpleCookie()
    with contextlib.suppress(CookieError):
        jar.load(fmt.get("cookies") or "")
    if cookie := "; ".join(f"{name}={morsel.value}" for name, morsel in jar.items()):
        headers["Cookie"] = cookie
    return headers


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
