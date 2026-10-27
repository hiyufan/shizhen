"""独立渲染 Spine 区域附件动画；复杂约束与网格明确拒绝，不静默丢失。"""

from __future__ import annotations

import base64
import math
import re
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from . import spine_data, spine_pose


def _check(value, depth: int = 0) -> None:
    if depth > 30:
        raise ValueError("Spine 结构嵌套过深")
    if isinstance(value, dict):
        for item in value.values():
            _check(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 20_000:
            raise ValueError("Spine 数组超过限制")
        for item in value:
            _check(item, depth + 1)
    elif isinstance(value, (int, float)) and (not math.isfinite(value) or abs(value) > 1_000_000):
        raise ValueError("Spine 数值无效或超过限制")


def _duration(value) -> float:
    if isinstance(value, dict):
        return max([float(value.get("time", 0))] + [_duration(item) for item in value.values()])
    if isinstance(value, list):
        return max((_duration(item) for item in value), default=0)
    return 0


def _skin(data: dict) -> dict:
    skins = data.get("skins") or {}
    if isinstance(skins, list):
        chosen = next((row for row in skins if row.get("name") == "default"), skins[0] if skins else {})
        return chosen.get("attachments") or {}
    return skins.get("default") or next(iter(skins.values()), {})


def _rgba(value: str) -> tuple:
    if not re.fullmatch(r"[a-fA-F0-9]{8}", value):
        raise ValueError("Spine 颜色字段无效")
    return tuple(int(value[index : index + 2], 16) / 255 for index in range(0, 8, 2))


def _color(slot: dict, row: dict, animation: dict, time: float, modern: bool) -> tuple:
    color = _rgba(slot.get("color", "ffffffff"))
    timelines = animation.get("slots", {}).get(slot["name"], {})
    if set(timelines) - {"attachment", "color", "rgba", "alpha"}:
        raise ValueError("这份 Spine 使用尚未支持的插槽时间轴")
    sampled = spine_pose.sample(timelines.get("rgba", []), ("r", "g", "b", "a"), time, modern, 1)
    if timelines.get("color"):
        rows = [
            frame | dict(zip(("r", "g", "b", "a"), _rgba(frame.get("color", "ffffffff")), strict=True))
            for frame in timelines["color"]
        ]
        sampled = spine_pose.sample(rows, ("r", "g", "b", "a"), time, modern, 1)
    color = sampled or color
    alpha = spine_pose.sample(timelines.get("alpha", []), ("value",), time, modern, 1)
    if alpha:
        color = color[:3] + alpha
    attached = _rgba(row.get("color", "ffffffff"))
    return tuple(max(0, min(1, a * b)) for a, b in zip(color, attached, strict=True))


def _item(slot: dict, row: dict, bone: tuple, region: dict, color: tuple) -> dict:
    if slot.get("blend", "normal") != "normal":
        raise ValueError("这份 Spine 使用特殊混合模式，暂时不支持转换")
    width, height = float(row.get("width", region["originalWidth"])), float(row.get("height", region["originalHeight"]))
    sx, sy = width / region["originalWidth"], height / region["originalHeight"]
    local = spine_pose.transform(
        row.get("x", 0), row.get("y", 0), row.get("rotation", 0), row.get("scaleX", 1), row.get("scaleY", 1)
    )
    x, y = -width / 2 + region["offsetX"] * sx, -height / 2 + region["offsetY"] * sy
    texture = (region["width"] * sx, 0, 0, -region["height"] * sy, x, y + region["height"] * sy)
    return {
        "region": row.get("path"),
        "matrix": spine_pose.multiply(spine_pose.multiply(bone, local), texture),
        "color": color,
    }


def _pose(data: dict, animation: dict, skin: dict, regions: dict, time: float, modern: bool) -> list[dict]:
    bones = spine_pose.bones(data, animation, time, modern)
    result = []
    for slot in spine_pose.order(data["slots"], animation, time):
        name = spine_pose.attachment(slot, animation, time)
        row = skin.get(slot["name"], {}).get(name)
        if not row or row.get("type") in {"boundingbox", "point"}:
            continue
        if row.get("type", "region") != "region" or row.get("sequence"):
            raise ValueError("暂时支持 Spine 区域附件动画；网格、裁切和序列附件尚不支持")
        path = row.get("path", name)
        if path not in regions or slot.get("bone") not in bones:
            raise ValueError("Spine 附件图像或骨骼缺失")
        result.append(
            _item(
                slot,
                row | {"path": path},
                bones[slot["bone"]],
                regions[path],
                _color(slot, row, animation, time, modern),
            )
        )
    return result


def _bounds(poses: list[list[dict]]) -> tuple:
    points = []
    for pose in poses:
        for item in pose:
            a, b, c, d, x, y = item["matrix"]
            points.extend(((x, y), (a + x, b + y), (c + x, d + y), (a + c + x, b + d + y)))
    if not points:
        raise ValueError("这份 Spine 动画没有可见的区域附件")
    xs, ys = zip(*points, strict=True)
    left, bottom, right, top = min(xs), min(ys), max(xs), max(ys)
    if not all(math.isfinite(v) for v in (left, bottom, right, top)) or min(right - left, top - bottom) <= 0:
        raise ValueError("Spine 动画显示范围无效")
    return left, bottom, right, top


def _canvas(bounds: tuple, count: int) -> tuple:
    left, bottom, right, top = bounds
    width, height = right - left, top - bottom
    scale = min(1, 1020 / max(width, height), math.sqrt(95_000_000 / (width * height * count)))
    return (
        math.ceil(width * scale) + 4,
        math.ceil(height * scale) + 4,
        (scale, 0, 0, -scale, 2 - left * scale, 2 + top * scale),
    )


def _matrix(value: tuple) -> str:
    return "matrix(" + " ".join(str(v) for v in value) + ")"


def _svg(pose: list[dict], images: dict[str, str], canvas: tuple) -> str:
    width, height, matrix = canvas
    root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "width": str(width), "height": str(height)})
    defs = SubElement(root, "defs")
    group = SubElement(root, "g", {"transform": _matrix(matrix)})
    for index, item in enumerate(pose):
        attributes = {
            "href": images[item["region"]],
            "width": "1",
            "height": "1",
            "preserveAspectRatio": "none",
            "transform": _matrix(item["matrix"]),
        }
        if item["color"] != (1, 1, 1, 1):
            filt = SubElement(defs, "filter", {"id": f"tint{index}", "color-interpolation-filters": "sRGB"})
            red, green, blue, alpha = item["color"]
            SubElement(
                filt,
                "feColorMatrix",
                {"type": "matrix", "values": f"{red} 0 0 0 0 0 {green} 0 0 0 0 0 {blue} 0 0 0 0 0 {alpha} 0"},
            )
            attributes["filter"] = f"url(#tint{index})"
        SubElement(group, "image", attributes)
    return tostring(root, encoding="unicode")


def render(source: Path, directory: Path, animation_name: str = "") -> tuple[int, float]:
    try:
        from resvg_py import svg_to_bytes
    except ImportError as exc:
        raise ValueError("Spine 转换需要安装 qiuyuantu[stickers]") from exc
    data, atlas, files = spine_data.skeleton(spine_data.archive(source))
    _check(data)
    if any(data.get(key) for key in ("ik", "transform", "path", "physics")):
        raise ValueError("暂时支持 Spine 区域附件动画，IK、路径及物理约束尚不支持")
    name = animation_name or next(iter(data["animations"]))
    animation = data["animations"].get(name)
    if not isinstance(animation, dict):
        raise ValueError("没有找到选择的 Spine 动画")
    if set(animation) - {"bones", "slots", "drawOrder", "draworder", "events"}:
        raise ValueError("这份 Spine 包含尚未支持的动画时间轴")
    duration = _duration(animation)
    rate, count = 30.0, math.ceil(duration * 30)
    if not 2 <= count <= 600:
        raise ValueError("Spine 动画需要 2–600 帧，最长 20 秒")
    regions = spine_data.regions(files, atlas)
    skin = _skin(data)
    modern = data["skeleton"]["spine"].startswith("4.")
    poses = [_pose(data, animation, skin, regions, index / rate, modern) for index in range(count)]
    canvas = _canvas(_bounds(poses), count)
    images = {
        key: "data:image/png;base64," + base64.b64encode(region["data"]).decode("ascii")
        for key, region in regions.items()
    }
    for index, pose in enumerate(poses):
        (directory / f"frame{index:04d}.png").write_bytes(
            svg_to_bytes(svg_string=_svg(pose, images, canvas), skip_system_fonts=True)
        )
    return count, rate
