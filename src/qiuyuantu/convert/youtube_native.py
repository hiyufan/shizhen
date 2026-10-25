"""YouTube 兜底：先经备用出口下 HLS，不行再开原生浏览器会话。已验证文件复用现有 Source 缓存。"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import re
import shutil
import signal
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from fractions import Fraction
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import progress
from ..parser.errors import ParseError
from ..utils import fallback_proxy, proxy_for, run_ytdlp, youtube_proxy
from . import config, net, store

log = logging.getLogger("uvicorn.error")
_HOST = "youtube-cache.invalid"
_ASSET = re.compile(r"https://youtube-cache\.invalid/([0-9a-f]{16})\.mp4\Z")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_FORMAT = re.compile(r"youtube-native:(audio|144|240|360|480|720|1080|1440|2160)\Z")
_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240, 144)
_results: dict[str, dict] = {}
# 解析时等浏览器名额最多等几秒：解析总共只有 90 秒，排太久轮到了也下不完
_PARSE_SLOT_WAIT = 10
# HLS 没走通时至少给浏览器留这么多秒（浏览器路径下一条 10 分钟 720p 约 28 秒）
_BROWSER_RESERVE = 30
# 状态页自检：不读缓存、拿最低清晰度（只验证这条路还通，省流量）
_FRESH: ContextVar[bool] = ContextVar("youtube_native_fresh", default=False)
_STAGES = {
    "chromium_ready": "browser",
    "navigation": "browser",
    "download_start": "downloading",
    "download": "downloading",
    "merging": "merging",
    "verify_decode_start": "verifying",
}


def enabled() -> bool:
    return (
        os.name == "posix"
        and os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE", "1") != "0"
        and bool(config.YOUTUBE_NATIVE_PYTHON)
        and os.access(config.YOUTUBE_NATIVE_PYTHON, os.X_OK)
        and config.YOUTUBE_NATIVE_SCRIPT.is_file()
    )


def video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = parsed.path.strip("/").split("/")
    value = ""
    if host == "youtu.be":
        value = parts[0]
    elif (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host in ("youtube-nocookie.com", "www.youtube-nocookie.com")
    ):
        if parsed.path == "/watch":
            value = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(parts) == 2 and parts[0] in ("shorts", "embed", "live", "v"):
            value = parts[1]
    return value if parsed.scheme in ("http", "https") and _VIDEO_ID.fullmatch(value) else None


def should_try(err: Exception, url: str) -> bool:
    from ..parser.errors import classify

    return bool(
        enabled() and video_id(url) and classify(err).reason in ("blocked", "parse", "empty", "timeout", "network")
    )


def asset_url(sid: str) -> str:
    return f"https://{_HOST}/{sid}.mp4"


def is_asset_url(url: str) -> bool:
    return bool(_ASSET.fullmatch(url))


def source_for_asset(url: str) -> store.Source | None:
    match = _ASSET.fullmatch(url)
    if not match or match[1] not in _results:
        return None
    return store.get(match[1])


def handles_format(url: str, format_spec: str) -> bool:
    return bool(video_id(url) and _FORMAT.fullmatch(format_spec))


def cached(url: str, key: str = "parse") -> tuple[store.Source, dict] | None:
    """缓存命中不等待其它视频的浏览器任务；不同分享形式复用同一个视频 ID。

    key：解析结果用 "parse"，下载任务按清晰度（"1080"），状态页自检时一律不命中。"""
    if not enabled() or _FRESH.get() or not (vid := video_id(url)):
        return None
    sid = store.source_id_for("youtube-native", vid, key)
    if sid in _results and (src := store.get(sid)):
        return src, _results[sid]
    return None


@contextlib.contextmanager
def fresh() -> Iterator[None]:
    """状态页自检用：缓存会续期，一直命中的话 YouTube 封了也报正常。"""
    token = _FRESH.set(True)
    try:
        yield
    finally:
        _FRESH.reset(token)


class Refused(ParseError):
    """备用线路明确做不了（太大、正忙）。比原先的风控报错更能告诉用户该怎么办，要原样往外报。"""


def link_or_copy(src: str | Path, dest: Path) -> None:
    """同一块盘上建硬链接，几百 MB 的原视频不用再复制一份；跨盘才真复制。"""
    if Path(src).resolve() == dest.resolve():
        return
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


async def check_media(info) -> None:
    """读取 1 KB 后关闭连接，拒绝只有元数据、实际媒体不可读的结果。"""
    from ..parser.errors import ParseError

    if not info.video_url:
        raise ParseError("empty", "没有可读取的视频直链")
    if is_asset_url(info.video_url):
        src = source_for_asset(info.video_url)
        if src:
            with open(src.path, "rb") as media:
                if media.read(1024):
                    return
        raise ParseError("empty", "YouTube 媒体已过期或没有数据")
    headers = net.headers_for(info.video_url) | info.video_headers | {"Range": "bytes=0-1023"}
    async with (
        net.safe_client(for_url=info.video_url, follow_redirects=True, timeout=10) as client,
        client.stream("GET", info.video_url, headers=headers) as response,
    ):
        response.raise_for_status()
        if response.headers.get("content-type", "").startswith("text/"):
            raise ParseError("empty", "媒体地址返回了文本页面")
        async for chunk in response.aiter_raw(1024):
            if chunk:
                return
    raise ParseError("empty", "媒体地址没有返回数据")


@functools.cache
def _semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(max(1, config.YOUTUBE_NATIVE_CONCURRENCY))


def _kill_group(group: int, sig: int) -> None:
    if group > 1 and group != os.getpgrp():
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, sig)


async def _stop_worker(process, groups: set[int]) -> None:
    for group in groups - {process.pid}:
        _kill_group(group, signal.SIGTERM)
    try:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(signal.SIGINT)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), 5)
    finally:
        for group in groups:
            _kill_group(group, signal.SIGKILL)
        await process.wait()


def _report_stage(event: dict) -> None:
    if stage := _STAGES.get(event.get("stage")):
        progress.emit(stage, fallback=True)


class _Events:
    """下载器 stdout 的一行行 JSON：记下要用的结果，其余转给阶段提示和进度条。"""

    def __init__(self, pid: int, on_event: Callable[[dict], None] | None) -> None:
        self.groups = {pid}
        self.on_event = on_event
        self.verified: dict | None = None
        self.too_large: dict | None = None
        self.error = "YouTube 原生会话未完成下载"

    def feed(self, line: bytes) -> None:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        _report_stage(event)
        if self.on_event:
            self.on_event(event)
        stage = event.get("stage")
        if stage == "too_large":
            self.too_large = event
        elif stage == "_process" and isinstance(event.get("group"), int):
            self.groups.add(event["group"])
        elif stage == "verified":
            self.verified = event
        elif stage == "failed":
            self.error = str(event.get("error") or self.error)[:350]

    def refusal(self) -> Refused | None:
        return _too_large(self.too_large) if self.too_large else None


def _too_large(e: dict) -> Refused:
    minutes = float(e.get("duration") or 0) / 60
    size, limit = int(e.get("bytes") or 0) / 1e6, int(e.get("limit") or 0) / 1e6
    return Refused(
        "too_large", f"{minutes:.0f} 分钟的视频在 {e.get('height')}p 约 {size:.0f} MB，超过了 {limit:.0f} MB"
    )


async def _worker(
    vid: str,
    height: int,
    work: Path,
    proxy: str,
    *,
    fit: bool = False,
    max_bytes: int | None = None,
    timeout: float | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    command = [
        config.YOUTUBE_NATIVE_PYTHON,
        str(config.YOUTUBE_NATIVE_SCRIPT),
        vid,
        "--output",
        str(work),
        "--height",
        str(height),
        "--max-bytes",
        str(max_bytes or config.MAX_SOURCE_BYTES),
        "--report-resources",
        "--activate",
    ]
    if fit:
        command.append("--fit")
    if proxy:
        command += ["--proxy", proxy]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    events = _Events(process.pid, on_event)
    groups = events.groups

    async def read() -> None:
        async for line in process.stdout:
            events.feed(line)
        await process.wait()

    try:
        await asyncio.wait_for(read(), timeout or config.YOUTUBE_NATIVE_TIMEOUT)
        if refusal := events.refusal():
            raise refusal
        verified, error = events.verified, events.error
        if process.returncode != 0 or not verified:
            raise RuntimeError(error)
        return verified
    finally:
        # Chromium / Xvfb 各有自己的进程组；只杀这次 helper 明确登记的组。
        await _stop_worker(process, groups)


class _HlsHooks:
    """yt-dlp 下 HLS 时的回调：事件格式和下载器 stdout 的一样，进度条、阶段提示照用。"""

    def __init__(self, max_bytes: int, on_event: Callable[[dict], None] | None) -> None:
        self.max_bytes = max_bytes
        self.on_event = on_event
        # 超时 / 取消时 asyncio 不再等线程，靠这个让 yt-dlp 在下一次进度回调时停下来
        self.stop = threading.Event()
        self.done: dict[str, int] = {}
        self.last = 0.0

    def emit(self, stage: str, **fields) -> None:
        event = {"stage": stage, **fields}
        _report_stage(event)
        if self.on_event:
            self.on_event(event)

    def check(self) -> None:
        import yt_dlp

        if self.stop.is_set():
            raise yt_dlp.utils.DownloadCancelled("YouTube HLS 下载已取消")

    def progress(self, d: dict) -> None:
        self.check()
        key = d.get("filename") or (d.get("info_dict") or {}).get("format_id", "video")
        self.done[key] = max(self.done.get(key, 0), d.get("downloaded_bytes") or 0)
        total = sum(self.done.values())
        if total > self.max_bytes:
            raise RuntimeError("原视频超过服务器大小上限")
        if time.monotonic() - self.last >= 1 or d.get("status") == "finished":
            self.last = time.monotonic()
            self.emit("bytes", done=total)

    def postprocess(self, d: dict) -> None:
        if d.get("status") == "started" and d.get("postprocessor") == "Merger":
            self.emit("merging")


class _NoHls(RuntimeError):
    """这个视频 / 清晰度没有能用的 HLS 格式，直接开浏览器。"""


# HLS 音轨在 media group 里，yt-dlp 不填 acodec / tbr（2026-10-22 实测），按 itag 认：码率和对应的 https 音轨
_HLS_AUDIO_KBPS = {"233": 48, "234": 128}
_HLS_AUDIO_HTTPS = {"233": "139", "234": "140"}


def _audio_kbps(f: dict) -> float:
    return f.get("tbr") or f.get("abr") or _HLS_AUDIO_KBPS.get(str(f.get("format_id")), 0)


def _hls_formats(formats: list[dict], height: int) -> tuple[dict | None, dict[int, dict]]:
    """(码率最高的 HLS 音轨, {清晰度: 该清晰度码率最高的 HLS 画面})。1080p 以内只要 H.264，和浏览器路径一致。"""
    hls = [f for f in formats if f.get("protocol") == "m3u8_native" and f.get("url")]
    audio = max(
        (f for f in hls if f.get("vcodec") == "none" and f.get("acodec") != "none"),
        key=_audio_kbps,
        default=None,
    )
    videos: dict[int, dict] = {}
    for f in hls:
        h, vcodec = int(f.get("height") or 0), str(f.get("vcodec") or "none")
        if not h or h > height or vcodec == "none" or f.get("acodec") not in (None, "none"):
            continue
        if h <= 1080 and not vcodec.startswith("avc1"):
            continue
        if (f.get("tbr") or 0) >= (videos.get(h, {}).get("tbr") or 0):
            videos[h] = f
    return audio, videos


def _track_key(f: dict) -> tuple[int, str, bool]:
    vcodec = str(f.get("vcodec") or "")
    family = "vp9" if vcodec.startswith(("vp9", "vp09")) else vcodec[:4]
    return int(f.get("height") or 0), family, (f.get("fps") or 30) > 30


def _hls_plan(info: dict, height: int, max_bytes: int, *, fit: bool) -> tuple[str, int, int]:
    """(yt-dlp 格式, 清晰度, 估算字节)。规则同下载器的 plan：fit=True 放不下就降一档，最低一档也放不下就直说。

    HLS 的 tbr 是峰值码率，按它估比实际大两倍多（实测 720p60 估 361 MB、实际 161 MB）。同一视频的
    https 分轨带准确大小，同清晰度、同编码、同帧率档的内容一样大（161 MB 对 160 MB），优先用它。"""
    duration = float(info.get("duration") or 0)
    formats = info.get("formats") or []
    audio, videos = _hls_formats(formats, height)
    if duration <= 0 or not audio or not videos:
        raise _NoHls("没有可用的 HLS 格式")
    exact: dict = {}
    for f in formats:
        if f.get("protocol") != "m3u8_native" and (n := f.get("filesize")):
            key = f.get("format_id") if f.get("vcodec") == "none" else _track_key(f)
            exact[key] = max(exact.get(key, 0), n)

    def estimate(key, kbps: float) -> int:
        return int(exact.get(key) or kbps * 125 * duration)  # tbr 是 kbit/s

    audio_bytes = estimate(_HLS_AUDIO_HTTPS.get(str(audio.get("format_id"))), _audio_kbps(audio) or 128)

    def size(h: int) -> int:
        return estimate(_track_key(videos[h]), videos[h].get("tbr") or 0) + audio_bytes

    candidates = sorted(videos, reverse=True)
    for h in candidates if fit else candidates[:1]:
        total = size(h)
        if total <= max_bytes:
            return f"{videos[h]['format_id']}+{audio['format_id']}", h, total
    h = candidates[-1] if fit else candidates[0]
    raise _too_large({"height": h, "bytes": size(h), "duration": duration, "limit": max_bytes})


async def _verify(path: Path, expected: float, max_bytes: int):
    """同下载器里的校验：时长对得上、音视频都在；整条解封装一遍查缺段，画面抽开头 / 中间 / 结尾各解码 2 秒。"""
    from . import ffmpeg

    if not path.is_file() or not 1024 <= path.stat().st_size <= max_bytes:
        raise RuntimeError("HLS 没有下完整，或者超过了大小上限")
    media = await ffmpeg.probe(path)
    if not media.height or not media.has_audio or abs(media.duration - expected) > 2:
        raise RuntimeError("音轨、视频轨或完整时长校验失败")
    await ffmpeg.run(["-xerror", "-i", str(path), "-map", "0", "-c", "copy", "-f", "null", "-"])
    for start in (0, max(0, media.duration / 2 - 1), max(0, media.duration - 3)):
        await ffmpeg.run(["-xerror", "-ss", f"{start:.2f}", "-i", str(path), "-t", "2", "-f", "null", "-"])
    return media


async def _hls(
    vid: str,
    height: int,
    work: Path,
    proxy: str,
    *,
    fit: bool = False,
    max_bytes: int | None = None,
    timeout: float | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    """不开浏览器：经同一出口用 yt-dlp 并发拉 HLS 分片。返回和下载器 verified 一样的结果。

    媒体地址绑定出口 IP，解析和下载用同一个 proxy。"""
    import yt_dlp

    from . import ffmpeg

    max_bytes = max_bytes or config.MAX_SOURCE_BYTES
    hooks = _HlsHooks(max_bytes, on_event)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 8,
        "ffmpeg_location": ffmpeg.ffmpeg_dir(),
        "merge_output_format": "mp4",
        "outtmpl": str(work / "video.%(ext)s"),
        **config.ytdlp_cookie_opts(),
        **config.ytdlp_runtime_opts(),
    }
    if proxy:
        opts["proxy"] = proxy

    def run() -> dict:
        # process=False：先拿全部格式自己挑，再交给第二个实例按挑好的格式下，不重复请求 YouTube
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/watch?v=" + vid, download=False, process=False)
        if not info or info.get("id") != vid:
            raise RuntimeError("yt-dlp 返回了其它视频")
        spec, h, expected = _hls_plan(info, height, max_bytes, fit=fit)
        hooks.emit("plan", height=h, bytes=expected, duration=float(info["duration"]))
        hooks.check()
        hooks.emit("download_start")
        params = opts | {"format": spec, "progress_hooks": [hooks.progress], "postprocessor_hooks": [hooks.postprocess]}
        with yt_dlp.YoutubeDL(params) as ydl:
            ydl.process_ie_result(info, download=True)
        return info

    future = asyncio.ensure_future(run_ytdlp(run))
    try:
        info = await asyncio.wait_for(asyncio.shield(future), timeout or config.YOUTUBE_NATIVE_TIMEOUT)
    except BaseException:
        hooks.stop.set()
        # 等线程收手再返回：调用方接着要清空 / 删掉工作目录
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(future, 15)
        raise
    path = work / "video.mp4"
    hooks.emit("verify_decode_start")
    media = await _verify(path, float(info["duration"]), max_bytes)
    return _hls_result(vid, path, info, media)


def _hls_result(vid: str, path: Path, info: dict, media) -> dict:
    thumbnails = sorted(info.get("thumbnails") or [], key=lambda t: (t.get("preference") or -100, t.get("width") or 0))
    return {
        "id": vid,
        "via": "hls",
        "file": str(path),
        "bytes": path.stat().st_size,
        "duration": media.duration,
        "title": info.get("title") or "YouTube",
        "author": info.get("uploader") or info.get("channel") or "",
        "channel_id": info.get("channel_id") or "",
        "thumbnail": thumbnails[-1].get("url", "") if thumbnails else "",
        "heights": sorted(
            {int(f["height"]) for f in info.get("formats") or [] if f.get("height") and f.get("vcodec") != "none"}
        ),
        "streams": [
            {"codec_type": "video", "width": media.width, "height": media.height, "avg_frame_rate": str(media.fps)},
            {"codec_type": "audio"},
        ],
    }


def _clear(work: Path) -> None:
    for p in work.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def _proxy(url: str) -> str:
    return (
        os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE_PROXY")
        or youtube_proxy(url)
        or fallback_proxy()
        or proxy_for("youtube")
        or ""
    )


async def _try_hls(
    vid: str, height: int, work: Path, proxy: str, *, job: bool, timeout: float, on_event: Callable[[dict], None] | None
) -> dict | None:
    """HLS 走通返回结果；没走通清掉半截文件、返回 None，由调用方开浏览器。"""
    if not config.YOUTUBE_HLS:
        return None
    try:
        return await _hls(
            vid,
            height,
            work,
            proxy,
            fit=not job,
            max_bytes=config.MAX_SOURCE_BYTES if job else config.YOUTUBE_HLS_PARSE_BYTES,
            timeout=timeout - _BROWSER_RESERVE,
            on_event=on_event,
        )
    except Refused as err:
        # 解析时连最低一档都放不下，浏览器那边只会更放不下；下载任务的估算偏保守，
        # 交给浏览器按 player 响应里的准确大小再判断
        if not job:
            raise
        log.info("YouTube HLS 估算放不下，改开浏览器 vid=%s err=%s", vid, err)
    except Exception as err:  # noqa: BLE001 - 什么原因都改开浏览器，原因记日志
        log.info("YouTube HLS 没走通，改开浏览器 vid=%s err=%s", vid, str(err)[:200])
    _clear(work)
    return None


async def ensure(
    url: str, height: int = 720, *, key: str = "", job: bool = False, on_event: Callable[[dict], None] | None = None
) -> tuple[store.Source, dict]:
    """下好并校验一份原视频：先试 HLS（不开浏览器，十秒上下），没走通再开浏览器走 SABR。

    解析时（job=False）要在 90 秒的解析时限里下完：估算超过大小预算自动降清晰度，浏览器名额最多等几秒。
    下载 / 转换任务（job=True）按用户选的清晰度、用任务的时限。"""
    vid = video_id(url)
    if not vid or height not in _HEIGHTS or not enabled():
        raise RuntimeError("YouTube 原生会话兜底未配置或链接不受支持")
    key = key or str(height)
    if hit := cached(url, key):
        progress.emit("cached")
        return hit
    sid = store.source_id_for("youtube-native", vid, key)
    config.ensure_dirs()
    proxy = _proxy(url)
    timeout = config.YOUTUBE_NATIVE_JOB_TIMEOUT if job else config.YOUTUBE_NATIVE_TIMEOUT
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix=".youtube-native-", dir=config.SOURCES_DIR) as tmp:
        work = Path(tmp).resolve()
        result = await _try_hls(vid, height, work, proxy, job=job, timeout=timeout, on_event=on_event)
        if result is None:
            sem = _semaphore()
            if sem.locked():
                progress.emit("queued", fallback=True)
            try:
                await asyncio.wait_for(sem.acquire(), None if job else _PARSE_SLOT_WAIT)
            except asyncio.TimeoutError:
                raise Refused("timeout", "YouTube 备用线路正忙，过一分钟再试") from None
            try:
                if hit := cached(url, key):
                    return hit
                progress.emit("browser", fallback=True)
                budget = (
                    {"max_bytes": config.MAX_SOURCE_BYTES}
                    if job
                    else {"fit": True, "max_bytes": config.YOUTUBE_NATIVE_PARSE_BYTES}
                )
                remaining = max(1.0, deadline - time.monotonic())
                result = await _worker(vid, height, work, proxy, timeout=remaining, on_event=on_event, **budget)
            finally:
                sem.release()
        return _register(sid, vid, work, result)


def _register(sid: str, vid: str, work: Path, result: dict) -> tuple[store.Source, dict]:
    """校验下载结果，搬进 Source 缓存。"""
    path = Path(result.get("file") or "").resolve()
    streams = result.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    valid = (
        result.get("id") == vid
        and work in path.parents
        and path.is_file()
        and 1024 <= path.stat().st_size <= config.MAX_SOURCE_BYTES
        and float(result.get("duration") or 0) > 0
        and video.get("height")
        and any(s.get("codec_type") == "audio" for s in streams)
    )
    if not valid:
        raise RuntimeError("YouTube 兜底没有返回经过验证的目标音视频")
    dest = config.SOURCES_DIR / f"{sid}.mp4"
    path.replace(dest)
    fps = 0.0
    with contextlib.suppress(ValueError, ZeroDivisionError):
        fps = float(Fraction(video.get("avg_frame_rate") or "0"))
    src = store.put(
        store.Source(
            id=sid,
            path=str(dest),
            title=result.get("title") or "YouTube",
            duration=float(result["duration"]),
            width=int(video.get("width") or 0),
            height=int(video["height"]),
            fps=fps,
        )
    )
    _results[sid] = result | {"file": str(dest)}
    # Source 被正常清理后，相应的元数据也释放。
    for k in list(_results):
        if k != sid and not Path(_results[k]["file"]).is_file():
            _results.pop(k, None)
    return src, _results[sid]


async def parse(url: str):
    from ..parser.base import FormatInfo, VideoAuthor, VideoInfo

    probing = _FRESH.get()
    src, result = await ensure(url, 144 if probing else 720, key="probe" if probing else "parse")
    heights = set(result.get("heights") or [])
    formats = [
        FormatInfo(label=f"{h}p", height=h, ext="mp4", format_spec=f"youtube-native:{h}")
        for h in _HEIGHTS
        if h > src.height and h in heights
    ]
    formats.append(FormatInfo(label="仅音频", ext="m4a", height=0, format_spec="youtube-native:audio"))
    return VideoInfo(
        source="youtube",
        page_url=f"https://www.youtube.com/watch?v={result['id']}",
        video_url=asset_url(src.id),
        title=src.title,
        cover_url=result.get("thumbnail") or "",
        duration=src.duration,
        width=src.width,
        height=src.height,
        formats=formats,
        author=VideoAuthor(uid=result.get("channel_id") or "", name=result.get("author") or ""),
    )


def _job_progress(report: Callable[[float, str], None] | None) -> Callable[[dict], None] | None:
    """把下载器的事件翻成任务进度条：估算总大小来自 plan，已下字节来自 bytes。"""
    if report is None:
        return None
    expected = [0]

    def on_event(event: dict) -> None:
        stage = event.get("stage")
        if stage == "plan":
            expected[0] = int(event.get("bytes") or 0)
            report(0.02, "原视频搬运中")
        elif stage == "bytes" and expected[0]:
            report(min(0.9, max(0.02, int(event.get("done") or 0) / expected[0] * 0.9)), "原视频搬运中")
        elif stage == "merging":
            report(0.92, "把声音和画面缝到一起")
        elif stage == "verify_decode_start":
            report(0.95, "检查一下有没有缺帧")

    return on_event


async def download(
    url: str, format_spec: str, out_dir: Path, stem: str, report: Callable[[float, str], None] | None = None
) -> Path:
    from . import ffmpeg

    match = _FORMAT.fullmatch(format_spec)
    audio = bool(match and match[1] == "audio")
    height = int(match[1]) if match and not audio else 720
    hit = cached(url)
    if hit and (audio or hit[0].height == height):
        # 解析时下好的那份正好能用：仅音频从任何一份里抽都一样
        src = hit[0]
    elif audio:
        src, _ = await ensure(url, 360, key="audio", job=True, on_event=_job_progress(report))
    else:
        src, _ = await ensure(url, height, job=True, on_event=_job_progress(report))
    out_dir.mkdir(parents=True, exist_ok=True)
    if audio:
        dest = out_dir / f"{stem}.m4a"
        await ffmpeg.run(["-i", src.path, "-vn", "-c:a", "copy", str(dest)])
    else:
        dest = out_dir / f"{stem}.mp4"
        await asyncio.to_thread(link_or_copy, src.path, dest)
    return dest
