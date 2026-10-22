"""YouTube 原生会话兜底。隔离下载器，已验证文件复用现有 Source 缓存。"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import re
import shutil
import signal
import tempfile
from fractions import Fraction
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..utils import fallback_proxy, proxy_for, youtube_proxy
from . import config, net, store

_HOST = "youtube-cache.invalid"
_ASSET = re.compile(r"https://youtube-cache\.invalid/([0-9a-f]{16})\.mp4\Z")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_FORMAT = re.compile(r"youtube-native:(audio|144|240|360|480|720|1080|1440|2160)\Z")
_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240, 144)
_results: dict[str, dict] = {}


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


def cached(url: str, height: int = 720) -> tuple[store.Source, dict] | None:
    """缓存命中不等待其它视频的浏览器任务；不同分享形式复用同一个视频 ID。"""
    if not enabled() or not (vid := video_id(url)):
        return None
    sid = store.source_id_for("youtube-native", vid, str(height))
    if sid in _results and (src := store.get(sid)):
        return src, _results[sid]
    return None


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
    return asyncio.Semaphore(1)


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


async def _worker(vid: str, height: int, work: Path, proxy: str) -> dict:
    command = [
        config.YOUTUBE_NATIVE_PYTHON,
        str(config.YOUTUBE_NATIVE_SCRIPT),
        vid,
        "--output",
        str(work),
        "--height",
        str(height),
        "--max-bytes",
        str(config.MAX_SOURCE_BYTES),
        "--report-resources",
        "--activate",
    ]
    if proxy:
        command += ["--proxy", proxy]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    groups = {process.pid}
    verified, error = None, "YouTube 原生会话未完成下载"

    async def read() -> None:
        nonlocal verified, error
        async for line in process.stdout:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("stage") == "_process" and isinstance(event.get("group"), int):
                groups.add(event["group"])
            elif event.get("stage") == "verified":
                verified = event
            elif event.get("stage") == "failed":
                error = str(event.get("error") or error)[:350]
        await process.wait()

    try:
        await asyncio.wait_for(read(), config.YOUTUBE_NATIVE_TIMEOUT)
        if process.returncode != 0 or not verified:
            raise RuntimeError(error)
        return verified
    finally:
        # Chromium / Xvfb 各有自己的进程组；只杀这次 helper 明确登记的组。
        await _stop_worker(process, groups)


async def ensure(url: str, height: int = 720) -> tuple[store.Source, dict]:
    vid = video_id(url)
    if not vid or height not in _HEIGHTS or not enabled():
        raise RuntimeError("YouTube 原生会话兜底未配置或链接不受支持")
    if hit := cached(url, height):
        return hit
    sid = store.source_id_for("youtube-native", vid, str(height))
    async with _semaphore():
        if hit := cached(url, height):
            return hit
        config.ensure_dirs()
        proxy = (
            os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE_PROXY")
            or youtube_proxy(url)
            or fallback_proxy()
            or proxy_for("youtube")
            or ""
        )
        with tempfile.TemporaryDirectory(prefix=".youtube-native-", dir=config.SOURCES_DIR) as tmp:
            work = Path(tmp).resolve()
            result = await _worker(vid, height, work, proxy)
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
        for key in list(_results):
            if key != sid and not Path(_results[key]["file"]).is_file():
                _results.pop(key, None)
        return src, _results[sid]


async def parse(url: str):
    from ..parser.base import FormatInfo, VideoAuthor, VideoInfo

    src, result = await ensure(url)
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


async def download(url: str, format_spec: str, out_dir: Path, stem: str) -> Path:
    from . import ffmpeg

    match = _FORMAT.fullmatch(format_spec)
    height = int(match[1]) if match and match[1] != "audio" else 720
    src, _ = await ensure(url, height)
    out_dir.mkdir(parents=True, exist_ok=True)
    if match and match[1] == "audio":
        dest = out_dir / f"{stem}.m4a"
        await ffmpeg.run(["-i", src.path, "-vn", "-c:a", "copy", str(dest)])
    else:
        dest = out_dir / f"{stem}.mp4"
        if Path(src.path).resolve() != dest.resolve():
            await asyncio.to_thread(shutil.copyfile, src.path, dest)
    return dest
