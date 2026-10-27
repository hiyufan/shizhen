"""央视 MP4 分段完整下载与流复制合并；默认 HLS 不能代表最高档位。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from ..parser import mp4_probe
from ..parser.base import FormatInfo
from ..parser.cctv import CCTV, chapter_formats, chapters
from ..utils import current_source, run_ytdlp
from . import config, ffmpeg
from .fetch import TooLarge, fetch_bytes, too_large_message, ytdlp_download

Report = Callable[[float, str], None]


def handles(page_url: str) -> bool:
    parsed = urlparse(page_url)
    return parsed.hostname in {"tv.cctv.com", "tv.cctv.cn"} or (
        parsed.hostname == "vdn.apps.cntv.cn" and parsed.path == "/api/getHttpVideoInfo.do"
    )


async def _selection(data: dict, spec: str, *, fit: bool) -> FormatInfo | None:
    formats = await chapter_formats(data)
    if not fit:
        selected = next((fmt for fmt in formats if fmt.format_spec == spec), None)
        if selected is None:
            raise ValueError("来源当前未提供所选清晰度，请重新解析后选择")
        if selected.filesize > config.MAX_SOURCE_BYTES:
            raise TooLarge(too_large_message(selected.filesize, config.MAX_SOURCE_BYTES, False))
        return selected
    candidates = [fmt for fmt in formats if fmt.height <= config.SOURCE_MAX_HEIGHT]
    selected = next((fmt for fmt in candidates if fmt.filesize <= config.MAX_SOURCE_BYTES), None)
    if selected:
        return selected
    if candidates:
        raise TooLarge(too_large_message(min(f.filesize for f in candidates), config.MAX_SOURCE_BYTES, True))
    return None


async def _check(path: Path, expected: FormatInfo, duration: float) -> None:
    metadata = await asyncio.to_thread(mp4_probe.local_metadata, path)
    if metadata is None:
        raise ValueError("央视网视频没有完整的 MP4 轨道信息")
    actual, seconds = metadata
    if (actual.width, actual.height, actual.codec) != (expected.width, expected.height, expected.codec):
        raise ValueError("央视网视频分段的清晰度或编码不一致，请重新解析")
    if abs(seconds - duration) > 1:
        raise ValueError("央视网视频没有下载完整，请重新解析再试")


async def _merge(data: dict, selected: FormatInfo, out: Path, report: Report) -> None:
    parts = chapters(data, selected.format_spec.split(":")[1])
    size = 0
    with TemporaryDirectory(prefix="cctv-", dir=out.parent) as directory:
        work = Path(directory)
        for index, part in enumerate(parts):
            path = work / f"{index:04d}.mp4"
            if size >= config.MAX_SOURCE_BYTES:
                raise TooLarge("视频超过下载上限，换低一档清晰度试试")
            await fetch_bytes(part.url, path, limit=config.MAX_SOURCE_BYTES - size)
            size += path.stat().st_size
            await _check(path, selected, part.duration)
            report((index + 1) / len(parts) * 0.88, "原视频搬运中")
        manifest = work / "parts.ffconcat"
        manifest.write_text("ffconcat version 1.0\n" + "".join(f"file '{i:04d}.mp4'\n" for i in range(len(parts))))
        report(0.92, "把视频分段合在一起")
        await ffmpeg.run(
            [
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(manifest),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
        await _check(out, selected, sum(p.duration for p in parts))
        if out.stat().st_size > config.MAX_SOURCE_BYTES:
            raise TooLarge("视频超过下载上限，换低一档清晰度试试")


async def download(
    page_url: str,
    format_spec: str,
    out_dir: Path,
    stem: str,
    report: Report,
    *,
    fit: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    token = current_source.set("cctv")
    out = out_dir / f"{stem}.mp4"
    try:
        data = await CCTV().video_data(page_url)
        selected = None if format_spec == "cctv:hls" else await _selection(data, format_spec, fit=fit)
        if selected is None:
            # 只用已经确认可解码的 hls_url；不能让提取器改选帧级加扰的 manifest。
            hls = data.get("hls_url")
            if not hls:
                raise ValueError("央视网没有提供 HLS 播放地址")
            spec = (
                f"bv*[height<={config.SOURCE_MAX_HEIGHT}]+ba/b[height<={config.SOURCE_MAX_HEIGHT}]"
                if fit
                else "bv*+ba/b"
            )
            return await run_ytdlp(
                ytdlp_download,
                hls,
                spec,
                out_dir,
                stem,
                report=report,
                cancelled=cancelled,
                fit=fit,
            )
        await _merge(data, selected, out, report)
        return out
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        current_source.reset(token)
