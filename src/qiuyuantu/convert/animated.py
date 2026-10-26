"""将真实多帧图片准备成转换用的视频，不依赖扩展名或 CDN 的 Content-Type。"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, UnidentifiedImageError

from . import ffmpeg


def _write_frames(source: Path, directory: Path, stopped: threading.Event) -> float:
    try:
        image = Image.open(source)
    except UnidentifiedImageError as exc:
        raise ValueError("下载到的文件不是可用的动态图片") from exc
    with image:
        count = getattr(image, "n_frames", 1)
        if count < 2:
            raise ValueError("这张封面是静态图片，可以直接下载原图，无需转换")
        if count > 600 or image.width * image.height * count > 100_000_000:
            raise ValueError("动态图片超过帧数或像素限制")
        duration = 0.0
        entries = []
        for index in range(count):
            if stopped.is_set():
                return 0.0
            image.seek(index)
            image.load()  # WebP 在解码后才提供当前帧的 duration。
            seconds = max(0.01, float(image.info.get("duration", 100)) / 1000)
            duration += seconds
            if duration > 30:
                raise ValueError("动态图片超过 30 秒，暂不支持转换")
            name = f"frame{index:04d}.png"
            image.convert("RGBA").save(directory / name)
            entries.extend((f"file '{name}'", f"duration {seconds:.6f}"))
        entries.append(f"file '{name}'")
        (directory / "frames.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
        return duration


async def prepare(source: Path, destination: Path) -> None:
    # 取消时先停止并等解码线程退出，再删除帧目录，避免清理与写入交叉。
    with TemporaryDirectory(prefix="animation-", dir=source.parent) as temporary:
        directory = Path(temporary)
        stopped = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(_write_frames, source, directory, stopped))
        try:
            duration = await asyncio.shield(task)
        except asyncio.CancelledError:
            stopped.set()
            await task
            raise
        try:
            await ffmpeg.run(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    str(directory / "frames.txt"),
                    "-t",
                    f"{duration:.6f}",
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-vsync",
                    "vfr",
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
