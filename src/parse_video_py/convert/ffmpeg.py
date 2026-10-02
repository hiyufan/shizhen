"""Thin async wrapper around the ffmpeg binary.

We prefer an ffmpeg on PATH; otherwise the one bundled with imageio-ffmpeg is
copied into data/bin so yt-dlp can find it under its normal name too.
"""

from __future__ import annotations

import asyncio
import functools
import os
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from . import config

ProgressCb = Callable[[float], Awaitable[None] | None]


@functools.cache
def ffmpeg_path() -> str:
    if found := shutil.which("ffmpeg"):
        return found
    local = config.BIN_DIR / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if not local.exists():
        import imageio_ffmpeg  # bundled binary, ~80MB, no ffprobe

        config.BIN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), local)
    return str(local)


def ffmpeg_dir() -> str:
    return str(Path(ffmpeg_path()).parent)


def _local_inputs(args: list[str]) -> list[str]:
    """每个 -i 前面加 -protocol_whitelist file：输入只能是本机文件。

    处理的都是用户上传 / 平台给的不可信媒体，文件里引用的 http / tcp / rtmp 地址一律不让 ffmpeg 去连。
    ffmpeg 的 HLS 等解复用器默认也有限制，这里写死成我们自己的规则，不靠它的实现细节。
    """
    out: list[str] = []
    for arg in args:
        if arg == "-i":
            out += ["-protocol_whitelist", "file"]
        out.append(arg)
    return out


@dataclass
class ProbeInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    rotation: int = 0


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VID_RE = re.compile(r"Video:.*?\s(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_ROT_RE = re.compile(r"rotate\s*:\s*(-?\d+)|rotation of (-?\d+(?:\.\d+)?) degrees")


async def probe(path: str | os.PathLike) -> ProbeInfo:
    """Parse `ffmpeg -i` stderr — we don't ship ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_path(),
        "-hide_banner",
        *_local_inputs(["-i", str(path)]),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    text = err.decode("utf-8", "replace")
    info = ProbeInfo()
    if m := _DUR_RE.search(text):
        h, mnt, s = m.groups()
        info.duration = int(h) * 3600 + int(mnt) * 60 + float(s)
    if m := _VID_RE.search(text):
        info.width, info.height = int(m.group(1)), int(m.group(2))
    if m := _FPS_RE.search(text):
        info.fps = float(m.group(1))
    if m := _ROT_RE.search(text):
        info.rotation = int(float(m.group(1) or m.group(2)))
        if info.rotation % 180 != 0:
            info.width, info.height = info.height, info.width
    info.has_audio = "Audio:" in text
    return info


def threads_per_job() -> int:
    """几个任务并行时把核心分摊开，免得互相抢到都很慢。"""
    if config.FFMPEG_THREADS > 0:
        return config.FFMPEG_THREADS
    return max(1, (os.cpu_count() or 2) // max(1, config.MAX_CONCURRENT_JOBS))


def _progress_fraction(line: bytes, total: float | None) -> float | None:
    """`-progress` 输出里的 out_time_us= / out_time_ms= 行换算成 0~0.99；别的行返回 None。"""
    text = line.decode("utf-8", "replace").strip()
    if not total or not text.startswith(("out_time_us=", "out_time_ms=")):
        return None
    try:
        us = int(text.split("=", 1)[1])  # ffmpeg labels both keys in microseconds
    except ValueError:
        return None
    return max(0.0, min(0.99, us / 1_000_000 / total))


async def run(args: list[str], total: float | None = None, on_progress: ProgressCb | None = None) -> None:
    """Run ffmpeg, streaming `-progress` output into on_progress(0..1).

    任务被取消（超时 / 用户取消）时会把 ffmpeg 一起杀掉，不留孤儿进程。
    """
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-y",
        "-nostats",
        "-loglevel",
        "error",
        "-threads",
        str(threads_per_job()),
        "-progress",
        "pipe:1",
        *_local_inputs(args),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    last = -1.0
    try:
        while line := await proc.stdout.readline():
            frac = _progress_fraction(line, total) if on_progress else None
            if frac is None or frac - last < 0.01:
                continue
            last = frac
            r = on_progress(frac)
            if asyncio.iscoroutine(r):
                await r
        _, err = await proc.communicate()
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0:
        msg = err.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("ffmpeg 失败: " + (msg[-1] if msg else f"exit {proc.returncode}"))


def _even_scale(width: int | None, max_height: int | None = None) -> str:
    """Scale expression keeping aspect ratio and even dimensions (required by yuv420p)."""
    if width:
        return f"scale={width}:-2:flags=lanczos"
    if max_height:
        return f"scale=-2:'min({max_height},ih)':flags=lanczos"
    return "scale=trunc(iw/2)*2:trunc(ih/2)*2"


async def make_gif(
    src: str,
    dst: str,
    *,
    start: float,
    duration: float,
    fps: int,
    width: int,
    dither: str = "bayer",
    speed: float = 1.0,
    colors: int = 256,
    on_progress: ProgressCb | None = None,
) -> None:
    dither_opt = {
        "bayer": "dither=bayer:bayer_scale=4",
        "sierra2_4a": "dither=sierra2_4a",
        "none": "dither=none",
    }.get(dither, "dither=bayer:bayer_scale=4")
    speed = max(0.25, min(4.0, speed))
    pts = "" if abs(speed - 1.0) < 1e-3 else f"setpts=PTS/{speed},"
    vf = (
        f"[0:v]{pts}fps={fps},{_even_scale(width)},split[a][b];"
        f"[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
        f"[b][p]paletteuse={dither_opt}:diff_mode=rectangle"
    )
    out_dur = duration / speed
    await run(
        [
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            src,
            "-filter_complex",
            vf,
            "-loop",
            "0",
            "-an",
            dst,
        ],
        total=out_dur,
        on_progress=on_progress,
    )


async def encode_segment(
    src: str,
    dst: str,
    *,
    start: float,
    duration: float,
    max_height: int = 1080,
    fps: int = 30,
    container: str = "mov",
    extra_metadata: dict[str, str] | None = None,
    on_progress: ProgressCb | None = None,
) -> None:
    """H.264 + AAC clip, the building block for live / motion photos."""
    args = [
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        src,
        "-vf",
        f"{_even_scale(None, max_height)},fps={fps}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart+use_metadata_tags",
        "-map_metadata",
        "-1",
    ]
    for k, v in (extra_metadata or {}).items():
        args += ["-metadata", f"{k}={v}"]
    args += ["-f", container, dst]
    await run(args, total=duration, on_progress=on_progress)


async def remux_live(src: str, dst: str, *, identifier: str) -> None:
    """平台自带的实况视频 (H.264 小片段) 不重编码, 直接换成 MOV 并写入 Apple 配对标识。"""
    await run(
        [
            "-i",
            src,
            "-c",
            "copy",
            "-map_metadata",
            "-1",
            "-movflags",
            "+faststart+use_metadata_tags",
            "-metadata",
            f"com.apple.quicktime.content.identifier={identifier}",
            "-f",
            "mov",
            dst,
        ]
    )


async def extract_frame(src: str, dst: str, *, at: float, max_height: int = 1080) -> None:
    await run(
        [
            "-ss",
            f"{at:.3f}",
            "-i",
            src,
            "-frames:v",
            "1",
            "-vf",
            _even_scale(None, max_height),
            "-q:v",
            "2",
            "-f",
            "image2",
            dst,
        ]
    )


async def filmstrip(src: str, dst: str, *, duration: float, frames: int = 16, height: int = 72) -> None:
    """One JPEG with `frames` thumbnails side by side — the trimmer's backdrop."""
    frames = max(2, min(40, frames))
    step = max(duration / frames, 0.04)
    await run(
        [
            "-i",
            src,
            "-vf",
            f"fps=1/{step:.4f},scale=-2:{height},tile={frames}x1",
            "-frames:v",
            "1",
            "-q:v",
            "5",
            "-f",
            "image2",
            dst,
        ]
    )
