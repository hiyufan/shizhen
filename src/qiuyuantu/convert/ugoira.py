"""有界 Pixiv 帧包准备；时序重新取自平台，不信任客户端传入的 ZIP 清单。"""

from __future__ import annotations

import asyncio
import re
import threading
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from PIL import Image

from ..parser.pixiv import Pixiv
from ..parser.pixiv_links import HOSTS
from . import config, ffmpeg
from .fetch import fetch_bytes

MAX_ZIP = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024


def handles(url: str) -> bool:
    p = urlparse(url)
    return p.hostname in HOSTS and bool(
        re.fullmatch(r"/(?:[a-z]{2}/)?artworks/(?:[0-9]+|unlisted/[A-Za-z0-9_-]+)", p.path)
    )


def _entries(archive: zipfile.ZipFile, frames: list[dict]) -> dict:
    if not 1 <= len(frames) <= 600 or sum(frame["delay"] for frame in frames) > 30_000:
        raise ValueError("动画超过 600 帧或 30 秒转换限制")
    rows = archive.infolist()
    names = [row.filename for row in rows]
    if len(names) != len(set(names)) or set(names) != {row["file"] for row in frames}:
        raise ValueError("动画帧包与时序列表不一致")
    if sum(row.file_size for row in rows) > MAX_EXPANDED:
        raise ValueError("动画帧包解压体积超过限制")
    for row in rows:
        if row.is_dir() or row.flag_bits & 1 or row.file_size > 8 * 1024 * 1024:
            raise ValueError("动画帧包包含无效或超限条目")
        if not re.fullmatch(r"[0-9]{6}\.(?:jpg|jpeg|png|gif)", row.filename):
            raise ValueError("动画帧文件名无效")
    return {row.filename: row for row in rows}


def _write_frames(source: Path, directory: Path, frames: list[dict], stopped: threading.Event) -> float:
    pixels, duration = 0, 0
    entries = []
    with zipfile.ZipFile(source) as archive:
        rows = _entries(archive, frames)
        for index, frame in enumerate(frames):
            if stopped.is_set():
                raise ValueError("任务已取消")
            # 读取到 EOF 会校验 CRC；固定输出名，不提取平台路径或链接。
            with archive.open(rows[frame["file"]]) as stream, Image.open(stream) as image:
                area = image.width * image.height
                pixels += area
                if area > 8_000_000 or pixels > 100_000_000 or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("动画帧像素或帧数超过限制")
                name = f"frame{index:04d}.png"
                image.convert("RGBA").save(directory / name)
                # PIL 可能只消费部分图片字节；继续读完，确保每项 CRC 校验执行。
                while stream.read(65536):
                    pass
            delay = frame["delay"]
            duration += delay
            entries.extend((f"file '{name}'", "option framerate 1000", f"duration {delay / 1000:.6f}"))
        entries.extend((f"file '{name}'", "option framerate 1000"))
    (directory / "frames.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
    return duration / 1000


async def _decode(source: Path, directory: Path, frames: list[dict]) -> float:
    stopped = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_write_frames, source, directory, frames, stopped))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stopped.set()
        await asyncio.gather(task, return_exceptions=True)
        raise


async def download(page_url: str, out_dir: Path, stem: str, report) -> Path:
    info = await Pixiv().parse_share_url(page_url)
    if not info.animation_frames or not info.assets or info.assets[0].ext != "zip":
        raise ValueError("这篇作品不是可转换的 Ugoira 动画")
    destination = out_dir / (stem + ".mp4")
    with TemporaryDirectory(prefix="ugoira-", dir=out_dir) as temporary:
        directory = Path(temporary)
        source = directory / "frames.zip"
        report(0.05, "正在下载动画帧包")
        await fetch_bytes(info.assets[0].url, source, limit=min(MAX_ZIP, config.MAX_SOURCE_BYTES))
        report(0.5, "正在读取动画的每一帧")
        duration = await _decode(source, directory, info.animation_frames)
        try:
            # 不提前 -t 裁剪，末帧的结束占位保留其完整停留时长。
            await ffmpeg.run(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(directory / "frames.txt"),
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-vsync",
                    "vfr",
                    "-enc_time_base",
                    "1:1000",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ],
                total=duration,
            )
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return destination
