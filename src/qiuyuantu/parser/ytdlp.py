"""
YouTube / TikTok / Instagram 以及其它没有专用解析器的站点, 统一交给 yt-dlp。

直链只挑「音视频合一」的 mp4 (浏览器能直接播放、代理能直接流式转发);
更高的清晰度需要服务端用 ffmpeg 合并, 放进 formats 里让前端按需下载。
"""

import asyncio
import contextlib
import logging
import re
import time
from http.cookies import CookieError, SimpleCookie
from typing import Any

from .. import progress
from ..convert import config as convert_config
from ..convert import fallback_exit, net
from ..convert.ffmpeg import ffmpeg_dir
from ..utils import bot_checked, fallback_proxy, is_youtube_url, proxy_for, run_ytdlp, wants_fallback, youtube_proxy
from . import instagram, tiktok
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import classify
from .quality import number, short_side

log = logging.getLogger("uvicorn.error")

# 已有原生兜底缓存的视频再次解析时，给备用出口这么久去换回完整结果，超时就用缓存
_REFRESH_SECONDS = 12


class _DirectBreaker:
    """机房出口被 YouTube 抽查时，直连常要先失败 1～3 秒才切到备用出口。

    连续被拦后暂时跳过直连；到期后放一次直连试探，成功就恢复，仍被拦则继续跳过。"""

    THRESHOLD = 2
    SKIP_SECONDS = 600

    def __init__(self) -> None:
        self.failures = 0
        self.until = 0.0

    def skipping(self) -> bool:
        return time.monotonic() < self.until

    def blocked(self) -> None:
        self.failures += 1
        if self.failures >= self.THRESHOLD:
            self.until = time.monotonic() + self.SKIP_SECONDS

    def reset(self) -> None:
        self.failures, self.until = 0, 0.0


_direct = _DirectBreaker()


async def _whole_carousel(info: dict[str, Any], share_url: str) -> VideoInfo | None:
    """全是视频的 Instagram 多图帖 yt-dlp 能成功，但只留得下第一条；页面里有整条帖子。
    含图片的多图帖 yt-dlp 会直接报错，走 _recover 的同一条页面兜底。拿不到时仍用 yt-dlp 的结果。"""
    if info.get("_entry_count", 1) < 2 or not instagram.post_code(share_url):
        return None
    with contextlib.suppress(Exception):
        return await instagram.image_post(share_url)
    return None


class YtDlp(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        """原生兜底缓存过的视频也先走常规路径：缓存只有兜底自己那几档，出口恢复后要能换回完整结果。"""
        from ..convert import youtube_native

        try:
            return await self._parse(share_url)
        except Exception as err:
            if not youtube_native.cached(share_url):
                raise
            log.info("YouTube 常规路径没走通，沿用兜底缓存：%s", _brief(err))
        return await youtube_native.parse(share_url)

    async def _parse(self, share_url: str) -> VideoInfo:
        guarded = is_youtube_url(share_url) and bool(fallback_proxy())
        if guarded and _direct.skipping():
            return await self._without_direct(share_url)
        try:
            info = await run_ytdlp(self._extract, share_url)
            result = await _whole_carousel(info, share_url) or await self._usable_info(info, share_url)
        except Exception as err:
            if guarded and _worth_fallback(err, share_url):
                _direct.blocked()
            return await self._recover(share_url, err)
        if guarded:
            _direct.reset()
        return result

    async def _without_direct(self, share_url: str) -> VideoInfo:
        from ..convert import youtube_native

        progress.emit("fallback", fallback=True)
        try:
            if youtube_native.enabled() and youtube_native.video_id(share_url):
                return await self._race_fallbacks(share_url)
            return await self._fallback_info(share_url)
        except Exception as err:
            # 备用出口自身出问题时，下一次请求重新试直连，不能一直绕开可能已恢复的默认出口。
            if classify(err).reason in ("blocked", "parse", "empty", "timeout", "network"):
                _direct.reset()
            raise

    async def _recover(self, share_url: str, err: Exception) -> VideoInfo:
        from ..convert import youtube_native

        # 图片帖会报 No video formats found 或 There is no video in this post；
        # 空媒体响应也可能只是 yt-dlp 的接口没拿到，公开页面仍然有原图。
        # 兜底失败不能盖掉原错误（例如本来是限龄，兜底却撞上网络超时）。
        # TikTok 图文：yt-dlp 只认视频，报 Unsupported URL；短链要先跟到落地页才知道是不是图文
        if tiktok.photo_post(share_url) or tiktok.is_short(share_url):
            progress.emit("fallback", fallback=True)
            with contextlib.suppress(Exception):
                if post := await tiktok.image_post(share_url):
                    return post
        if instagram.post_code(share_url):
            progress.emit("fallback", fallback=True)
            with contextlib.suppress(Exception):
                if post := await instagram.image_post(share_url):
                    return post
        if _worth_fallback(err, share_url):
            progress.emit("fallback", fallback=True)
            if youtube_native.should_try(err, share_url):
                return await self._race_fallbacks(share_url)
            try:
                return await self._fallback_info(share_url)
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
        """走备用出口解析；这个出口自己也被要求验证时换一个出口地址再试一次。"""
        seen = fallback_exit.generation()
        try:
            return await self._via_fallback(share_url)
        except Exception as err:
            if not (bot_checked(err) and await fallback_exit.renew(seen)):
                raise
        return await self._via_fallback(share_url)

    async def _via_fallback(self, share_url: str) -> VideoInfo:
        info = await run_ytdlp(self._extract, share_url, fallback_proxy())
        return await self._usable_info(info, share_url, fallback=True)

    async def _race_fallbacks(self, share_url: str) -> VideoInfo:
        """默认路径失败后并行恢复；只接受可读媒体或完整验证的缓存。"""
        from ..convert import youtube_native

        if youtube_native.cached(share_url):
            # 缓存随时可用，不必和它赛跑；失败或超时由 parse_share_url 退回缓存
            return await asyncio.wait_for(self._fallback_info(share_url), _REFRESH_SECONDS)
        fallback = asyncio.create_task(self._fallback_info(share_url))
        native = asyncio.create_task(youtube_native.parse(share_url))
        attempts = (fallback, native)
        try:
            for finished in asyncio.as_completed(attempts):
                try:
                    result = await finished
                except Exception:
                    if fallback.done() and (err := fallback.exception()) and _definitive(err, share_url):
                        raise err from None
                else:
                    _note_slow_recovery(fallback, native)
                    return result
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
        if proxy and proxy == fallback_proxy() and is_youtube_url(url):
            opts = convert_config.with_fallback_clients(opts)
        # yt-dlp 自己跟跳转、抓页面里嵌的地址；连内网由 net 里换掉的 getaddrinfo 拦
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        # 播放列表 / 多图帖只取第一条；记下条数，Instagram 多视频帖由调用方改用页面里的整条帖子
        count = 1
        while info and info.get("_type") in ("playlist", "multi_video") and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if not entries:
                break
            count = max(count, len(entries))
            info = entries[0]
        if not info:
            raise ValueError("yt-dlp 没有解析出内容")
        return {**info, "_entry_count": count} if count > 1 else info

    @staticmethod
    def _to_video_info(info: dict[str, Any], share_url: str) -> VideoInfo:
        formats = [f for f in (info.get("formats") or []) if f.get("url") and not f.get("has_drm")]
        video_url, headers, width, height = _direct_stream(info, formats)
        primary = next((f for f in formats if f["url"] == video_url), info)
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
            formats=_merge_options(formats, primary),
            video_headers=headers,
            video_fps=number(primary.get("fps")),
            video_bitrate=int(number(primary.get("tbr")) * 1000),
            video_codec=str(primary.get("vcodec") or ""),
            video_ext=str(primary.get("ext") or "mp4"),
        )


def specific_extractor(url: str) -> str:
    """yt-dlp 里专门认这个链接的提取器名；只有通用提取器认得时返回空串。会扫描全部提取器，放到线程里跑。"""
    from yt_dlp.extractor import gen_extractor_classes

    return next((ie.ie_key() for ie in gen_extractor_classes() if ie.ie_key() != "Generic" and ie.suitable(url)), "")


def _worth_fallback(err: Exception, share_url: str) -> bool:
    """默认出口的结果用不了时要不要换备用出口。

    除了明说被拦，YouTube 还会让默认出口「提取成功」却不给合流直链、或直链读不了（2026-11-10 复现：
    只报「没有可读取的视频直链」）。这时备用出口几秒就能拿到完整结果，不能只等慢好几倍的原生兜底。"""
    from ..convert import youtube_native

    return wants_fallback(err, share_url) or bool(fallback_proxy() and youtube_native.should_try(err, share_url))


def _brief(err: BaseException) -> str:
    return f"{classify(err).reason} {type(err).__name__}: {str(err)[:160]}"


def _note_slow_recovery(fallback: asyncio.Task, native: asyncio.Task) -> None:
    """原生兜底比备用出口慢好几倍、档位也少；它赢了说明备用出口有事，留一行日志备查。"""
    if not (native.done() and not native.cancelled() and native.exception() is None):
        return
    if not fallback.done():
        log.info("YouTube 备用出口还没返回，原生兜底先完成")
    elif not fallback.cancelled() and (err := fallback.exception()):
        log.info("YouTube 备用出口失败，改用原生兜底的结果：%s", _brief(err))


def _definitive(err: Exception, share_url: str) -> bool:
    """备用出口的这个报错能不能直接定论、不用等原生兜底：限龄、要登录这类是视频本身的问题。

    「已删除」不算：走到这一步，默认出口已经报了机器人验证，视频是在的；WARP 偶尔对正常视频
    回 Video unavailable（2026-10-22 iN1N8o3Miv4 被报成「已删除」，紧接着连试 15 次又全成功）。"""
    from ..convert import youtube_native

    return not youtube_native.should_try(err, share_url) and classify(err).reason != "deleted"


# 纯图片帖：yt-dlp 把图当 thumbnails 或 formats(ext=jpg) 返回
_IMAGE_EXTS = ("jpg", "jpeg", "png", "webp")
_KNOWN_SITES = ("youtube", "tiktok", "instagram", "vimeo", "facebook", "twitch", "reddit", "pinterest")


def _is_video(f: dict[str, Any]) -> bool:
    return f.get("vcodec") not in (None, "none", "images") and f.get("ext") not in _IMAGE_EXTS


def _is_audio(f: dict[str, Any]) -> bool:
    return f.get("acodec") not in (None, "none")


def _is_http(f: dict[str, Any]) -> bool:
    return (f.get("protocol") or "https").startswith("http") and not f.get("manifest_url")


def _direct_stream(info: dict[str, Any], formats: list[dict[str, Any]]) -> tuple[str, dict[str, str], int, int]:
    """浏览器能直接播的那一路：音视频合一的 http 直链，先按实际清晰度、帧率选择。
    返回 (地址, 要带的请求头, 宽, 高)；没有就是空地址。"""
    progressive = [f for f in formats if _is_video(f) and _is_audio(f) and _is_http(f)]
    best = max(
        reversed(progressive),  # 一样好的取后面那个（yt-dlp 按从差到好排）
        key=lambda f: (*_video_rank(f), f.get("ext") == "mp4"),
        default=None,
    )
    if best is None and info.get("url") and info.get("ext") in ("mp4", "mov", "webm"):
        best = info
    if best is None:
        return "", {}, 0, 0
    return best["url"], _stream_headers(best), int(number(best.get("width"))), int(number(best.get("height")))


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


def _merge_options(formats: list[dict[str, Any]], primary: dict[str, Any]) -> list[FormatInfo]:
    """列出来源真实存在的全部高清档位，不封顶 4K，也不虚构中间分辨率。"""
    videos = [f for f in formats if _is_video(f)]
    options = []
    heights = sorted({int(number(f.get("height"))) for f in videos}, reverse=True)
    any_h264 = any(_is_h264(f) for f in videos)
    for h in heights:
        candidates, h264 = _tier_candidates([f for f in videos if number(f.get("height")) == h], any_h264)
        best = max(candidates, key=lambda f: number(f.get("tbr")), default=None)
        if not h or best is None or _video_rank(best) <= _video_rank(primary):
            continue
        width = int(number(best.get("width")))
        approx = max((f.get("filesize") or f.get("filesize_approx") or 0 for f in candidates), default=0)
        options.append(
            FormatInfo(
                label=f"{short_side(width, h)}p"
                + (str(round(number(best.get("fps")))) if number(best.get("fps")) > 45 else ""),
                format_spec=_tier_spec(h, _codec_filter(best, h264)),
                ext="mp4",
                height=h,
                filesize=int(approx),
                width=width,
                fps=number(best.get("fps")),
                bitrate=int(number(best.get("tbr")) * 1000),
                codec=str(best.get("vcodec") or ""),
            )
        )
    if any(_is_audio(f) and not _is_video(f) for f in formats):
        options.append(FormatInfo(label="仅音频", format_spec="ba[ext=m4a]/ba", ext="m4a", height=0))
    return options


def _is_h264(f: dict[str, Any]) -> bool:
    return str(f.get("vcodec") or "").startswith("avc1")


def _tier_candidates(same_height: list[dict[str, Any]], any_h264: bool) -> tuple[list[dict[str, Any]], bool]:
    """同一清晰度里实际会下的那几路，和是否限定 H.264；返回空列表表示这一档不列。

    yt-dlp 默认把 AV1 / VP9 排在 H.264 前面，音轨也偏好 Opus：YouTube 1080p 会合成 AV1 + Opus 的 MP4，
    iPhone 的照片 App 存不进去，很多播放器也放不了。所以和 HLS、浏览器兜底一样，来源有 H.264 时
    1080p 以内只列 H.264：宁可少一档 60 帧，也不给 HDR 视频的 VP9.2 低档位、Shorts 里只有 VP9 的
    608p 这类多数设备放不了的文件。更高的 2K / 4K 来源只有 AV1 / VP9，先保帧率，同帧率有 H.264 才用它。"""
    sample = same_height[0]
    pool = same_height
    if any_h264 and short_side(int(number(sample.get("width"))), int(number(sample.get("height")))) <= 1080:
        pool = [f for f in same_height if _is_h264(f)]
    top = max((number(f.get("fps")) for f in pool), default=0)
    fastest = [f for f in pool if number(f.get("fps")) == top]
    preferred = [f for f in fastest if _is_h264(f)]
    return (preferred, True) if preferred else (fastest, False)


def _codec_filter(best: dict[str, Any], h264: bool) -> str:
    """把档位上标的编码写进格式串：H.264 不分档次；2K / 4K 锁定标出的那一路（不锁的话 yt-dlp 会换成 AV1）。
    编码名里只有字母、数字和点，原样放进 yt-dlp 的过滤条件。"""
    vcodec = str(best.get("vcodec") or "")
    if h264:
        return "[vcodec^=avc1]"
    return f"[vcodec={vcodec}]" if re.fullmatch(r"[\w.]+", vcodec) else ""


def _tier_spec(h: int, codec: str) -> str:
    """优先 AAC 音轨（m4a）；按编码挑不到时退回不限编码的写法。"""
    pick = f"[height={h}]"
    prefer = f"bv{pick}{codec}+ba[ext=m4a]/bv{pick}{codec}+ba/" if codec else ""
    return f"{prefer}bv{pick}+ba[ext=m4a]/bv{pick}+ba/b{pick}/bv{pick}"


def _video_rank(fmt: dict) -> tuple[float, float, float]:
    return number(fmt.get("height")), number(fmt.get("fps")), number(fmt.get("tbr"))


def _cover(info: dict[str, Any]) -> str:
    if cover := info.get("thumbnail"):
        return cover
    thumbnails = info.get("thumbnails") or []
    return thumbnails[-1].get("url", "") if thumbnails else ""


def _site_name(info: dict[str, Any]) -> str:
    extractor = (info.get("extractor_key") or info.get("extractor") or "ytdlp").lower()
    return next((key for key in _KNOWN_SITES if key in extractor), extractor)
