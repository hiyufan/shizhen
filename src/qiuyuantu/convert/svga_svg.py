"""将已校验的 SVGA 帧独立构造成 SVG；只嵌入本地校验后的 PNG。"""

from __future__ import annotations

import base64
import re
from xml.etree.ElementTree import Element, SubElement, tostring


def _matrix(transform: dict) -> str:
    return (
        "matrix("
        + " ".join(
            str(transform.get(name, 1 if name in {"a", "d"} else 0)) for name in ("a", "b", "c", "d", "tx", "ty")
        )
        + ")"
    )


def _path(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]*", value):
        raise ValueError("SVGA 路径包含无效指令")
    return value


def _color(value: dict | list | None) -> str:
    if not value:
        return "none"
    if isinstance(value, list):
        if len(value) != 4:
            raise ValueError("SVGA 颜色字段无效")
        value = dict(zip(("r", "g", "b", "a"), value, strict=True))
    channels = [max(0, min(255, round(float(value.get(name, 0)) * 255))) for name in ("r", "g", "b")]
    return f"rgba({channels[0]},{channels[1]},{channels[2]},{max(0, min(1, value.get('a', 0)))})"


def _style(value: dict) -> dict[str, str]:
    result = {
        "fill": _color(value.get("fill")),
        "stroke": _color(value.get("stroke")),
        "stroke-width": str(value.get("strokeWidth", 0)),
        "stroke-linecap": {0: "butt", 1: "round", 2: "square", "round": "round", "square": "square"}.get(
            value.get("lineCap"), "butt"
        ),
        "stroke-linejoin": {0: "miter", 1: "round", 2: "bevel", "round": "round", "bevel": "bevel"}.get(
            value.get("lineJoin"), "miter"
        ),
    }
    if value.get("miterLimit", 0) > 0:
        result["stroke-miterlimit"] = str(value["miterLimit"])
    if value.get("lineDash"):
        dash = value["lineDash"]
        if not isinstance(dash, list) or len(dash) > 32 or any(float(n) < 0 for n in dash):
            raise ValueError("SVGA 虚线字段无效")
        result["stroke-dasharray"] = " ".join(str(float(n)) for n in dash)
    elif value.get("lineDashI", 0) > 0:
        result["stroke-dasharray"] = f"{value['lineDashI']} {value.get('lineDashII', 0)}"
        result["stroke-dashoffset"] = str(value.get("lineDashIII", 0))
    return result


def _shape(parent: Element, row: dict) -> None:
    kind = row.get("type", 0)
    # 1.x 使用字符串名称，2.x 使用 protobuf 枚举。
    kind = {"shape": 0, "rect": 1, "ellipse": 2, "keep": 3}.get(kind, kind)
    args = row.get("args") or row.get({0: "shape", 1: "rect", 2: "ellipse"}.get(kind, ""), {})
    attributes = _style(row.get("styles") or {}) | {"transform": _matrix(row.get("transform") or {})}
    if kind == 0:
        SubElement(parent, "path", attributes | {"d": _path(args.get("d", ""))})
    elif kind == 1:
        SubElement(
            parent,
            "rect",
            attributes
            | {name: str(args.get(name, 0)) for name in ("x", "y", "width", "height")}
            | {"rx": str(args.get("cornerRadius", 0))},
        )
    elif kind == 2:
        SubElement(
            parent,
            "ellipse",
            attributes
            | {
                "cx": str(args.get("x", 0)),
                "cy": str(args.get("y", 0)),
                "rx": str(args.get("radiusX", 0)),
                "ry": str(args.get("radiusY", 0)),
            },
        )
    else:
        raise ValueError("SVGA 包含尚不支持的矢量类型")


def resolve_shapes(movie: dict) -> None:
    for sprite in movie["sprites"]:
        previous = None
        for frame in sprite["frames"]:
            shapes = frame.get("shapes", [])
            if shapes and shapes[0].get("type") in {3, "keep"}:
                if previous is None:
                    raise ValueError("SVGA KEEP 矢量缺少前一帧")
                # KEEP 是整个矢量列表的延续，不能只保留上一帧的第一个形状。
                frame["shapes"] = previous
            else:
                if any(shape.get("type") in {3, "keep"} for shape in shapes):
                    raise ValueError("SVGA KEEP 矢量位置无效")
                previous = shapes


def _sprite(parent: Element, sprite: dict, index: int, images: dict[str, str], token: str) -> None:
    frame = sprite["frames"][index]
    group = SubElement(
        parent, "g", {"transform": _matrix(frame.get("transform") or {}), "opacity": str(frame.get("alpha", 0))}
    )
    if path := frame.get("clipPath"):
        clip = SubElement(group, "clipPath", {"id": token})
        SubElement(clip, "path", {"d": _path(path)})
        group = SubElement(group, "g", {"clip-path": f"url(#{token})"})
    if image := images.get(sprite.get("imageKey")):
        box = frame.get("layout") or {}
        SubElement(
            group,
            "image",
            {
                "href": image,
                "width": str(box.get("width", 0)),
                "height": str(box.get("height", 0)),
                "preserveAspectRatio": "none",
            },
        )
    for shape in frame.get("shapes", []):
        _shape(group, shape)


def image_uris(movie: dict) -> dict[str, str]:
    return {
        key: "data:image/png;base64," + base64.b64encode(value).decode("ascii")
        for key, value in movie["images"].items()
    }


def frame(movie: dict, index: int, images: dict[str, str]) -> str:
    root = Element(
        "svg", {"xmlns": "http://www.w3.org/2000/svg", "width": str(movie["width"]), "height": str(movie["height"])}
    )
    sprites = {row.get("imageKey"): row for row in movie["sprites"]}
    matte_keys = {row.get("matteKey") for row in movie["sprites"] if row.get("matteKey")}
    defs = SubElement(root, "defs")
    for number, sprite in enumerate(movie["sprites"]):
        if sprite.get("imageKey") in matte_keys:
            continue
        parent = root
        if matte := sprite.get("matteKey"):
            if matte not in sprites:
                raise ValueError("SVGA 遮罩图层缺失")
            ident = f"mask{number}"
            mask = SubElement(
                defs,
                "mask",
                {
                    "id": ident,
                    "mask-type": "alpha",
                    "maskUnits": "userSpaceOnUse",
                    "x": "0",
                    "y": "0",
                    "width": str(movie["width"]),
                    "height": str(movie["height"]),
                },
            )
            _sprite(mask, sprites[matte], index, images, f"maskclip{number}")
            parent = SubElement(root, "g", {"mask": f"url(#{ident})"})
        _sprite(parent, sprite, index, images, f"clip{number}")
    return tostring(root, encoding="unicode")
