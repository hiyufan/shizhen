"""Spine 区域附件动画的独立时间轴和二维骨骼变换。"""

from __future__ import annotations

import math


def multiply(a: tuple, b: tuple) -> tuple:
    x, y, z, w, tx, ty = a
    j, k, horizontal, m, ux, uy = b
    return (
        x * j + z * k,
        y * j + w * k,
        x * horizontal + z * m,
        y * horizontal + w * m,
        x * ux + z * uy + tx,
        y * ux + w * uy + ty,
    )


def transform(x: float, y: float, rotation: float, sx: float, sy: float, shear: tuple = (0, 0)) -> tuple:
    r, s = math.radians(rotation + shear[0]), math.radians(rotation + 90 + shear[1])
    return math.cos(r) * sx, math.sin(r) * sx, math.cos(s) * sy, math.sin(s) * sy, x, y


def _bezier(a: float, b: float, c: float, d: float, t: float) -> float:
    return (1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t * t * c + t**3 * d


def _curve(row: dict, following: dict, key: str, component: int, t: float, modern: bool, default: float) -> float:
    start, end = float(row.get(key, default)), float(following.get(key, default))
    begin, finish = float(row.get("time", 0)), float(following.get("time", 0))
    ratio = min(1, max(0, (t - begin) / (finish - begin)))
    curve = row.get("curve")
    if curve == "stepped":
        return start
    if not isinstance(curve, list):
        return start + (end - start) * ratio
    points = curve[component * 4 : component * 4 + 4] if modern else curve[:4]
    if len(points) != 4:
        raise ValueError("Spine 贝塞尔时间轴无效")
    x1, y1, x2, y2 = map(float, points)
    x, first, last = (t, begin, finish) if modern else (ratio, 0, 1)
    low, high = 0.0, 1.0
    for _ in range(20):
        u = (low + high) / 2
        if _bezier(first, x1, x2, last, u) < x:
            low = u
        else:
            high = u
    value = _bezier(start if modern else 0, y1, y2, end if modern else 1, (low + high) / 2)
    return value if modern else start + (end - start) * value


def sample(rows: list, keys: tuple[str, ...], t: float, modern: bool, default: float = 0) -> tuple | None:
    if not rows or t < rows[0].get("time", 0):
        return None
    for index, row in enumerate(rows[:-1]):
        following = rows[index + 1]
        if following.get("time", 0) > t:
            return tuple(
                _curve(row, following, key, component, t, modern, default) for component, key in enumerate(keys)
            )
    return tuple(float(rows[-1].get(key, default)) for key in keys)


def _channels(bone: dict, timelines: dict, t: float, modern: bool) -> tuple:
    rotation_rows = timelines.get("rotate", [])
    rotation_key = "value" if any("value" in row for row in rotation_rows) else "angle"
    rotation = sample(rotation_rows, (rotation_key,), t, modern)
    translate = sample(timelines.get("translate", []), ("x", "y"), t, modern) or (0, 0)
    scale = sample(timelines.get("scale", []), ("x", "y"), t, modern, 1) or (1, 1)
    shear = sample(timelines.get("shear", []), ("x", "y"), t, modern) or (0, 0)
    tx, ty = bone.get("x", 0) + translate[0], bone.get("y", 0) + translate[1]
    sx, sy = bone.get("scaleX", 1) * scale[0], bone.get("scaleY", 1) * scale[1]
    return transform(
        tx,
        ty,
        bone.get("rotation", 0) + (rotation[0] if rotation else 0),
        sx,
        sy,
        (bone.get("shearX", 0) + shear[0], bone.get("shearY", 0) + shear[1]),
    )


def bones(data: dict, animation: dict, t: float, modern: bool) -> dict[str, tuple]:
    result = {}
    for bone in data["bones"]:
        name = bone["name"]
        if (
            bone.get("transform", "normal") != "normal"
            or bone.get("inherit", "normal") != "normal"
            or bone.get("inheritScale") is False
            or bone.get("inheritRotation") is False
        ):
            raise ValueError("这份 Spine 使用特殊骨骼继承，暂时不支持转换")
        timelines = animation.get("bones", {}).get(name, {})
        if set(timelines) - {"rotate", "translate", "scale", "shear"}:
            raise ValueError("这份 Spine 使用尚未支持的骨骼时间轴")
        local = _channels(bone, timelines, t, modern)
        parent = bone.get("parent")
        if parent and parent not in result:
            raise ValueError("Spine 骨骼层级缺失或顺序无效")
        result[name] = multiply(result[parent], local) if parent else local
    return result


def attachment(slot: dict, animation: dict, t: float) -> str | None:
    name = slot.get("attachment")
    rows = animation.get("slots", {}).get(slot["name"], {}).get("attachment", [])
    for row in rows:
        if row.get("time", 0) > t:
            break
        name = row.get("name")
    return name


def order(slots: list[dict], animation: dict, t: float) -> list[dict]:
    offsets = []
    for row in animation.get("drawOrder", animation.get("draworder", [])):
        if row.get("time", 0) > t:
            break
        offsets = row.get("offsets", [])
    if not offsets:
        return slots
    names = {slot["name"]: index for index, slot in enumerate(slots)}
    moved = {names[row["slot"]]: names[row["slot"]] + row["offset"] for row in offsets}
    result = [None] * len(slots)
    for original, target in moved.items():
        if not 0 <= target < len(slots) or result[target] is not None:
            raise ValueError("Spine 绘制顺序无效")
        result[target] = slots[original]
    rest = iter(slot for index, slot in enumerate(slots) if index not in moved)
    return [next(rest) if slot is None else slot for slot in result]
