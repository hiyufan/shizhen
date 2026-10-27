"""读取 MP4 的公开 vapc 配置，只还原不含外部融合资源的 VAP 动画。"""

from __future__ import annotations

import asyncio
import json
import math
import struct
from pathlib import Path

from . import ffmpeg


def metadata(source: Path) -> dict | None:
    total = source.stat().st_size
    offset = 0
    with source.open("rb") as stream:
        for _ in range(10_000):
            if offset + 8 > total:
                return None
            stream.seek(offset)
            size, name = struct.unpack(">I4s", stream.read(8))
            header = 8
            if size == 1:
                if offset + 16 > total:
                    return None
                size = struct.unpack(">Q", stream.read(8))[0]
                header = 16
            if size == 0:
                size = total - offset
            if size < header or offset + size > total:
                return None
            if name == b"vapc":
                if size - header > 1 << 20:
                    raise ValueError("VAP 配置超过大小限制")
                value = json.loads(stream.read(size - header))
                if not isinstance(value, dict):
                    raise ValueError("VAP 配置无效")
                return value
            offset += size
    raise ValueError("MP4 数据块数量超过限制")


def _rectangle(value, video_width: int, video_height: int) -> list[int]:
    if not isinstance(value, list) or len(value) != 4 or any(type(n) is not int for n in value):
        raise ValueError("VAP 颜色或透明区域无效")
    x, y, width, height = value
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > video_width or y + height > video_height:
        raise ValueError("VAP 区域超过视频尺寸")
    return value


def validate(data: dict) -> dict:
    info = data.get("info") or {}
    values = [info.get(k) for k in ("w", "h", "f", "fps", "videoW", "videoH")]
    if any(type(v) not in {int, float} or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("VAP 动画参数无效")
    width, height, count, rate, vw, vh = values
    if (
        max(width, height) > 1024
        or count > 600
        or rate > 60
        or count / rate > 30
        or width * height * count > 100_000_000
    ):
        raise ValueError("VAP 超过尺寸、帧数或时长限制")
    if max(vw, vh) > 4096 or vw * vh * count > 200_000_000 or count != int(count):
        raise ValueError("VAP 视频尺寸或帧数无效")
    if info.get("isVapx") or data.get("src") or any(row.get("obj") for row in data.get("frame", [])):
        raise ValueError("这份 VAP 需要额外融合素材，暂时只支持完整的独立动画")
    for key in ("aFrame", "rgbFrame"):
        _rectangle(info.get(key), int(vw), int(vh))
    return info


async def _render(source: Path, directory: Path, info: dict) -> None:
    actual = await ffmpeg.probe(source)
    if (actual.width, actual.height) != (info["videoW"], info["videoH"]):
        raise ValueError("VAP 视频尺寸与配置不匹配")
    if abs(actual.duration - info["f"] / info["fps"]) > max(0.2, 2 / info["fps"]):
        raise ValueError("VAP 视频时长与配置不匹配")
    alpha = ":".join(str(v) for v in [info["aFrame"][2], info["aFrame"][3], *info["aFrame"][:2]])
    rgb = ":".join(str(v) for v in [info["rgbFrame"][2], info["rgbFrame"][3], *info["rgbFrame"][:2]])
    scale = f"{int(info['w'])}:{int(info['h'])}"
    filters = (
        f"[0:v]split[c][a];[c]crop={rgb},scale={scale},format=rgb24[color];"
        f"[a]crop={alpha},scale={scale},format=gray[alpha];[color][alpha]alphamerge[out]"
    )
    await ffmpeg.run(
        [
            "-i",
            str(source),
            "-filter_complex",
            filters,
            "-map",
            "[out]",
            "-frames:v",
            str(int(info["f"])),
            "-vsync",
            "0",
            str(directory / "frame%04d.png"),
        ],
        total=info["f"] / info["fps"],
    )
    files = sorted(directory.glob("frame*.png"))
    if len(files) != int(info["f"]):
        raise ValueError("VAP 实际帧数与配置不匹配")
    for index, path in enumerate(files):
        path.rename(directory / f"tmp{index:04d}.png")
    for index in range(len(files)):
        (directory / f"tmp{index:04d}.png").rename(directory / f"frame{index:04d}.png")


def render(source: Path, directory: Path) -> tuple[int, float]:
    if source.stat().st_size > 32 << 20:
        raise ValueError("VAP 文件超过 32 MiB")
    data = metadata(source)
    if data is None:
        raise ValueError("文件缺少 VAP 的 vapc 配置，不能猜测透明区域")
    info = validate(data)
    asyncio.run(_render(source, directory, info))
    return int(info["f"]), float(info["fps"])
