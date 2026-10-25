"""从本地视频生成有时长、像素、体积和执行时间限制的私信 GIF。"""

import asyncio
import hashlib
import json
from pathlib import Path

from PIL import Image

from qiuyuantu.convert import ffmpeg


def native_video_key(message) -> str:
    return hashlib.sha256(f"native-motion:v1:{message.data['live_md5']}".encode()).hexdigest()


def gif_key(manifest: dict, settings) -> str:
    digests = [manifest["md5"][index] for index in gif_sources(manifest)]
    identity = f"gif:v2:{json.dumps(digests)}:{settings.gif_seconds}:{settings.gif_width}:{settings.gif_fps}"
    return hashlib.sha256(identity.encode()).hexdigest()


def gif_sources(manifest: dict) -> list[int]:
    if manifest.get("live_photo_count"):
        # 完整配对由调用者先用 live_pairs 校验；只转换对应视频，不把静态照片当作动画。
        return [video for _, video in manifest["live_pairs"]]
    return [index for index, name in enumerate(manifest["files"]) if name.endswith(".mp4")]


def gif_details(path: Path) -> dict:
    with Image.open(path) as image:
        return {
            "format": image.format,
            "frames": getattr(image, "n_frames", 1),
            "width": image.width,
            "height": image.height,
        }


async def encode_gif(source: Path, target: Path, settings) -> dict:
    for width, fps, colors in [
        (settings.gif_width, settings.gif_fps, 256),
        (min(settings.gif_width, 320), min(settings.gif_fps, 8), 128),
    ]:
        await asyncio.wait_for(
            ffmpeg.make_gif(
                str(source),
                str(target),
                start=0,
                duration=settings.gif_seconds,
                width=width,
                fps=fps,
                colors=colors,
                max_height=width,
                max_bytes=settings.job_bytes,
            ),
            timeout=90,
        )
        if 0 < target.stat().st_size <= settings.file_bytes:
            return await asyncio.to_thread(gif_details, target)
    raise ValueError("GIF 超过私信文件大小限制")
