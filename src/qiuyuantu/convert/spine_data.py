"""独立读取 Spine JSON 和图集 ZIP；不使用或分发 Spine Runtimes。"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image


def archive(source: Path) -> dict[str, bytes]:
    if source.stat().st_size > 32 << 20:
        raise ValueError("Spine ZIP 超过 32 MiB")
    with zipfile.ZipFile(source) as zipped:
        rows = zipped.infolist()
        if len(rows) > 250 or sum(row.file_size for row in rows) > 64 << 20:
            raise ValueError("Spine 帧包数量或解压大小超过限制")
        names = [row.filename for row in rows]
        if len(names) != len(set(names)):
            raise ValueError("Spine 帧包包含重复文件名")
        files = {}
        for row in rows:
            path = PurePosixPath(row.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in row.filename or row.flag_bits & 1:
                raise ValueError("Spine 帧包包含无效路径或加密文件")
            if row.file_size > 16 << 20:
                raise ValueError("Spine 单个文件超过大小限制")
            if not row.is_dir() and path.suffix.lower() in {".json", ".atlas", ".png", ".jpg", ".jpeg"}:
                files[row.filename] = zipped.read(row)
    return files


def skeleton(files: dict[str, bytes]) -> tuple[dict, str, dict[str, bytes]]:
    found = []
    for name, raw in files.items():
        if not name.endswith(".json"):
            continue
        if len(raw) > 4 << 20:
            raise ValueError("Spine JSON 超过大小限制")
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("bones"), list) and isinstance(data.get("slots"), list):
            found.append((data, name))
    if len(found) != 1:
        raise ValueError("Spine ZIP 需要一份骨骼 JSON、一个 atlas 和对应图片")
    data, name = found[0]
    atlases = [n for n in files if n.endswith(".atlas")]
    if len(atlases) != 1 or len(files[atlases[0]]) > 1 << 20:
        raise ValueError("Spine ZIP 需要一份可用的 atlas 图集描述")
    if not re.fullmatch(r"(?:3\.[5-8]|4\.[0-2])(?:\.[0-9]+)?", str(data.get("skeleton", {}).get("spine", ""))):
        raise ValueError("暂时支持 Spine 3.5–3.8 和 4.0–4.2 的 JSON 图集")
    if len(data["bones"]) > 200 or len(data["slots"]) > 200:
        raise ValueError("Spine 骨骼或插槽数量超过限制")
    animations = data.get("animations") or {}
    if not isinstance(animations, dict) or not 1 <= len(animations) <= 100:
        raise ValueError("Spine 动画列表为空或超过数量限制")
    return data, atlases[0], files


def describe(source: Path) -> list[str]:
    data, _, _ = skeleton(archive(source))
    names = list(data["animations"])
    if any(not isinstance(name, str) or len(name) > 100 for name in names):
        raise ValueError("Spine 动画名称无效")
    return names


def _blocks(text: str) -> list[tuple[str, dict[str, str]]]:
    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace() and ":" not in line:
            result.append((line.strip(), {}))
        elif result and ":" in line:
            key, value = line.strip().split(":", 1)
            result[-1][1][key] = value.strip()
    return result


def _ints(value: str) -> list[int]:
    values = [int(part.strip()) for part in value.split(",")]
    if any(abs(part) > 16384 for part in values):
        raise ValueError("Spine 图集参数超过限制")
    return values


def _region(image: Image.Image, data: dict) -> dict:
    box = _ints(data["bounds"]) if "bounds" in data else _ints(data.get("xy", "0,0")) + _ints(data.get("size", "0,0"))
    if len(box) != 4:
        raise ValueError("Spine 图集区域无效")
    x, y, width, height = box
    if data.get("rotate", "false") not in {"false", "0", "true", "90"}:
        raise ValueError("Spine 图集旋转角度尚不支持")
    rotated = data.get("rotate") in {"true", "90"}
    packed_width, packed_height = (height, width) if rotated else (width, height)
    if min(x, y) < 0 or min(width, height) <= 0 or x + packed_width > image.width or y + packed_height > image.height:
        raise ValueError("Spine 图集区域超出图片")
    tile = image.crop((x, y, x + packed_width, y + packed_height))
    if rotated:
        tile = tile.transpose(Image.Transpose.ROTATE_90)
    output = io.BytesIO()
    tile.save(output, format="PNG")
    offsets = (
        _ints(data["offsets"])
        if "offsets" in data
        else _ints(data.get("offset", "0,0")) + _ints(data.get("orig", f"{width},{height}"))
    )
    if len(offsets) != 4 or min(offsets[2:]) <= 0:
        raise ValueError("Spine 图集原始尺寸无效")
    return {
        "data": output.getvalue(),
        "width": width,
        "height": height,
        "offsetX": offsets[0],
        "offsetY": offsets[1],
        "originalWidth": offsets[2],
        "originalHeight": offsets[3],
    }


def regions(files: dict[str, bytes], atlas_name: str) -> dict:
    result = {}
    page = None
    pixels = 0
    region_pixels = 0
    directory = PurePosixPath(atlas_name).parent
    for name, data in _blocks(files[atlas_name].decode("utf-8-sig")):
        path = str(directory / name)
        if path in files and PurePosixPath(name).suffix.lower() in {".png", ".jpg", ".jpeg"}:
            with Image.open(io.BytesIO(files[path])) as image:
                pixels += image.width * image.height
                if pixels > 16_000_000 or max(image.size) > 4096:
                    raise ValueError("Spine 图集图片超过像素限制")
                page = image.convert("RGBA")
                if data.get("pma") == "true":
                    page = Image.frombytes("RGBa", page.size, page.tobytes()).convert("RGBA")
        elif page is not None:
            if name in result or len(result) >= 500:
                raise ValueError("Spine 图集区域重复或超过数量限制")
            result[name] = _region(page, data)
            region_pixels += result[name]["width"] * result[name]["height"]
            if region_pixels > 32_000_000:
                raise ValueError("Spine 图集区域总像素超过限制")
        else:
            raise ValueError("Spine 图集图片缺失，不会读取外部路径")
    return result
