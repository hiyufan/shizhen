"""SVGA 1.x / 2.x 的有界结构读取；不直接提取 ZIP 路径。"""

from __future__ import annotations

import io
import json
import math
import zipfile
import zlib
from pathlib import Path

from PIL import Image

from ..protobuf import fields

MAX_BYTES = 32 << 20
_LAYOUT = ("x", "y", "width", "height")
_TRANSFORM = ("a", "b", "c", "d", "tx", "ty")


def _mapping(data: bytes, names: tuple[str, ...]) -> dict:
    return {names[number - 1]: value for number, value in fields(data) if 1 <= number <= len(names)}


def _shape(data: bytes) -> dict:
    raw = dict(fields(data))
    kind = raw.get(1, 0)
    names = {0: ("d",), 1: ("x", "y", "width", "height", "cornerRadius"), 2: ("x", "y", "radiusX", "radiusY")}
    path = _mapping(raw.get(kind + 2, b""), names[kind]) if kind in names else {}
    if isinstance(path.get("d"), bytes):
        path["d"] = path["d"].decode("utf-8")
    style = _mapping(
        raw.get(10, b""),
        (
            "fill",
            "stroke",
            "strokeWidth",
            "lineCap",
            "lineJoin",
            "miterLimit",
            "lineDashI",
            "lineDashII",
            "lineDashIII",
        ),
    )
    for name in ("fill", "stroke"):
        if isinstance(style.get(name), bytes):
            style[name] = _mapping(style[name], ("r", "g", "b", "a"))
    transform = dict.fromkeys(_TRANSFORM, 0) | _mapping(raw[11], _TRANSFORM) if 11 in raw else {}
    return {"type": kind, "args": path, "styles": style, "transform": transform}


def _frame(data: bytes) -> dict:
    row: dict = {"shapes": []}
    for number, value in fields(data):
        if number == 1:
            row["alpha"] = value
        elif number in {2, 3} and isinstance(value, bytes):
            row["layout" if number == 2 else "transform"] = (
                _mapping(value, _LAYOUT) if number == 2 else dict.fromkeys(_TRANSFORM, 0) | _mapping(value, _TRANSFORM)
            )
        elif number == 4 and isinstance(value, bytes):
            row["clipPath"] = value.decode("utf-8")
        elif number == 5 and isinstance(value, bytes):
            row["shapes"].append(_shape(value))
    return row


def _sprite(data: bytes) -> dict:
    row: dict = {"frames": []}
    for number, value in fields(data):
        if number in {1, 3} and isinstance(value, bytes):
            row["imageKey" if number == 1 else "matteKey"] = value.decode("utf-8")
        elif number == 2 and isinstance(value, bytes):
            row["frames"].append(_frame(value))
    return row


def _protobuf(data: bytes) -> dict:
    movie: dict = {"images": {}, "sprites": []}
    for number, value in fields(data):
        if not isinstance(value, bytes):
            continue
        if number == 2:
            movie.update(_mapping(value, ("width", "height", "fps", "frames")))
        elif number == 3:
            row = dict(fields(value, limit=10))
            movie["images"][row.get(1, b"").decode("utf-8")] = row.get(2, b"")
        elif number == 4:
            movie["sprites"].append(_sprite(value))
        elif number == 5:
            raise ValueError("这份 SVGA 含音轨，暂时只支持无声动画，原文件仍可下载")
    return movie


def _zip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rows = archive.infolist()
        names = [row.filename for row in rows]
        if len(rows) > 300 or len(names) != len(set(names)) or sum(row.file_size for row in rows) > MAX_BYTES:
            raise ValueError("SVGA 帧包的数量或解压大小超过限制")
        if any(row.flag_bits & 1 or row.file_size > 8 << 20 for row in rows):
            raise ValueError("SVGA 帧包含加密或超限文件")
        if "movie.spec" not in names:
            raise ValueError("SVGA 1.x 帧包缺少 movie.spec")
        root = json.loads(archive.read("movie.spec"))
        params = root.get("movie") or {}
        box = params.get("viewBox") or {}
        movie = {
            "width": box.get("width"),
            "height": box.get("height"),
            "fps": params.get("fps"),
            "frames": params.get("frames"),
            "sprites": root.get("sprites") or [],
            "images": {},
        }
        for key, name in (root.get("images") or {}).items():
            if not isinstance(name, str) or "/" in name or "\\" in name:
                raise ValueError("SVGA 1.x 图像引用无效")
            filename = name if name in names else name + ".png"
            movie["images"][key] = archive.read(filename)
    return movie


def _image(data: bytes) -> bytes:
    if not isinstance(data, bytes) or len(data) > 8 << 20:
        raise ValueError("SVGA 图像超过大小限制")
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 4_000_000:
            raise ValueError("SVGA 图像格式或尺寸无效")
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        return output.getvalue()


def _check_numbers(value, depth: int = 0) -> None:
    if depth > 30:
        raise ValueError("SVGA 结构嵌套过深")
    if isinstance(value, dict):
        for item in value.values():
            _check_numbers(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 20_000:
            raise ValueError("SVGA 数组超过限制")
        for item in value:
            _check_numbers(item, depth + 1)
    elif isinstance(value, (int, float)) and (not math.isfinite(value) or abs(value) > 1_000_000):
        raise ValueError("SVGA 包含无效或超限数值")
    elif isinstance(value, str) and len(value) > 64_000:
        raise ValueError("SVGA 字段长度超过限制")


def _validate(movie: dict) -> dict:
    _check_numbers(movie)
    width, height, rate, count = (float(movie.get(key) or 0) for key in ("width", "height", "fps", "frames"))
    if not (0 < width <= 1024 and 0 < height <= 1024 and 0 < rate <= 60 and 2 <= count <= 600):
        raise ValueError("SVGA 尺寸、帧率或帧数超过限制")
    if count / rate > 30 or width * height * count > 100_000_000 or count != int(count):
        raise ValueError("SVGA 时长或总像素超过限制")
    if len(movie["images"]) > 200 or not 1 <= len(movie["sprites"]) <= 200:
        raise ValueError("SVGA 图层数量超过限制")
    if any(len(sprite.get("frames", [])) != int(count) for sprite in movie["sprites"]):
        raise ValueError("SVGA 图层的帧数与动画不匹配")
    movie["images"] = {key: _image(data) for key, data in movie["images"].items()}
    return movie


def read(source: Path) -> dict:
    if source.stat().st_size > 8 << 20:
        raise ValueError("SVGA 文件超过 8 MiB")
    data = source.read_bytes()
    if data.startswith(b"PK\x03\x04"):
        movie = _zip(data)
    else:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(data, MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("SVGA 压缩内容无效或超过限制")
        movie = _protobuf(raw)
    return _validate(movie)
