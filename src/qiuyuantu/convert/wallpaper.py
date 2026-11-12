"""Wallpaper Engine 包索引与素材提取；平台路径只作标签，永远不作为写入路径。"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from .wallpaper_tex import MAX_BYTES, Reader, decode, dimensions

MAX_ENTRIES = 2048
MAX_ASSETS = 100
MAX_OUTPUT = 256 << 20
IMAGE_TYPES = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".gif": "GIF"}


def entries(content: bytes) -> list[tuple[str, int, int]]:
    reader = Reader(content)
    if not re.fullmatch(r"PKGV\d{4}", reader.string()):
        raise ValueError("不是可识别的 Wallpaper Engine PKG 文件")
    count = reader.integer()
    if not 1 <= count <= MAX_ENTRIES:
        raise ValueError("壁纸包文件数量超过限制")
    rows = []
    names = set()
    for _ in range(count):
        name = reader.string().replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or ":" in name or "\0" in name or name in names:
            raise ValueError("壁纸包包含无效或重复的文件路径")
        names.add(name)
        rows.append((name, reader.integer(), reader.integer()))
    start = reader.position
    previous = 0
    for _, offset, size in sorted(rows, key=lambda row: row[1]):
        if offset < previous or size > MAX_BYTES or start + offset + size > len(content):
            raise ValueError("壁纸包文件范围无效或相互重叠")
        previous = offset + size
    return [(name, start + offset, size) for name, offset, size in rows]


def _image(content: bytes, suffix: str) -> None:
    image = Image.open(io.BytesIO(content))
    dimensions(*image.size)
    if image.format != IMAGE_TYPES[suffix]:
        raise ValueError("图片格式与文件扩展名不符")
    image.verify()


def _asset(content: bytes, suffix: str) -> tuple[bytes, str]:
    if suffix == ".tex":
        return decode(content)
    if suffix in IMAGE_TYPES:
        _image(content, suffix)
    elif suffix != ".mp4" or len(content) < 12 or content[4:8] != b"ftyp":
        raise ValueError("没有可提取的图片或视频")
    return content, suffix


def extract(source: Path, directory: Path, suffix: str, budget: int = MAX_OUTPUT * 2) -> None:
    if source.stat().st_size > MAX_BYTES:
        raise ValueError("壁纸文件超过 128 MB")
    content = source.read_bytes()
    rows = entries(content) if suffix == ".pkg" else [("texture.tex", 0, len(content))]
    result, skipped = [], []
    total = 0
    for name, offset, size in rows:
        extension = PurePosixPath(name).suffix.lower()
        if extension not in {*IMAGE_TYPES, ".tex", ".mp4"}:
            continue
        if len(result) >= MAX_ASSETS:
            raise ValueError("壁纸素材数量超过 100 个，请使用较小的包")
        try:
            data, ext = _asset(content[offset : offset + size], extension)
        except (ValueError, OSError, UnidentifiedImageError) as exc:
            skipped.append(
                {
                    "name": PurePosixPath(name).name,
                    "reason": str(exc) if isinstance(exc, ValueError) else "图片数据无效",
                }
            )
            continue
        total += len(data)
        if total > min(MAX_OUTPUT, budget // 2):
            raise ValueError("壁纸提取结果超过体积限制")
        filename = f"{len(result)}{ext}"
        (directory / filename).write_bytes(data)
        result.append(
            {
                "id": len(result),
                "name": PurePosixPath(name).stem + ext,
                "file": filename,
                "kind": "video" if ext == ".mp4" else "image",
                "size": len(data),
            }
        )
    if not result:
        if skipped:
            raise ValueError(skipped[0]["reason"])
        raise ValueError("包中没有支持的图片、TEX 或 MP4")
    with zipfile.ZipFile(directory / "assets.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        for item in result:
            archive.write(directory / item["file"], f"{item['id'] + 1}_{item['name']}")
    (directory / "manifest.json").write_text(
        json.dumps({"assets": result, "skipped": skipped}, ensure_ascii=False), encoding="utf-8"
    )
