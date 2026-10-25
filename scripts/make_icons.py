"""生成 Q 形图片标志：README 动画、导航、favicon 和 Apple 主屏图标共用路径与动效。

在仓库虚拟环境中运行：python scripts/make_icons.py。
修改 PATHS 后重新生成，避免不同入口的图标形状不一致；坐标基于 32×32 画布。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "qiuyuantu" / "static"
INK = "#1a1815"
SKY = "#6BAFDF"
BLUE = "#087BFF"
PAPER = "#fafaf9"  # 站点底色（site.css 的 --cold）；iOS 主屏图标不认透明，要垫底色
PART_NAMES = {SKY: "picture", INK: "frame", BLUE: "tail"}
# 时间比例、图片纵向位移、Q 尾笔沿对角线位移；首尾一致，播放后回到静态标志。
MOTION = ((0, 0, 0), (0.15, 0, -0.35), (0.42, -1.5, 1), (0.68, 0, 0), (1, 0, 0))
MOTION_SECONDS = 2.4

# 三次贝塞尔控制点同时用于 SVG 和位图；位图在放大画布上采样曲线后抗锯齿缩小。
PATHS = (
    (
        SKY,
        (
            ("M", (6, 17)),
            ("L", (10.8, 12.2)),
            ("C", (11.2, 11.8, 11.8, 11.8, 12.2, 12.2)),
            ("L", (17.5, 17.5)),
            ("L", (19.4, 15.6)),
            ("C", (19.8, 15.2, 20.4, 15.2, 20.8, 15.6)),
            ("L", (24, 18.8)),
            ("L", (24, 20.2)),
            ("L", (21.5, 20.2)),
            ("C", (19.7, 20.2, 18.7, 22.2, 20, 23.5)),
            ("L", (20.5, 24)),
            ("L", (9, 24)),
            ("C", (7.34, 24, 6, 22.66, 6, 21)),
            ("Z", ()),
        ),
    ),
    (
        INK,
        (
            ("M", (21.6, 28)),
            ("L", (9, 28)),
            ("C", (5.13, 28, 2, 24.87, 2, 21)),
            ("L", (2, 9)),
            ("C", (2, 5.13, 5.13, 2, 9, 2)),
            ("L", (21, 2)),
            ("C", (24.87, 2, 28, 5.13, 28, 9)),
            ("L", (28, 22.6)),
            ("C", (28, 23.2, 27.3, 23.5, 26.9, 23.1)),
            ("L", (24, 20.2)),
            ("L", (24, 9)),
            ("C", (24, 7.34, 22.66, 6, 21, 6)),
            ("L", (9, 6)),
            ("C", (7.34, 6, 6, 7.34, 6, 9)),
            ("L", (6, 21)),
            ("C", (6, 22.66, 7.34, 24, 9, 24)),
            ("L", (19.2, 24)),
            ("L", (22.1, 26.9)),
            ("C", (22.5, 27.3, 22.2, 28, 21.6, 28)),
            ("Z", ()),
        ),
    ),
    (
        BLUE,
        (
            ("M", (21.4, 21.4)),
            ("C", (22.15, 20.65, 23.35, 20.65, 24.1, 21.4)),
            ("L", (28.6, 25.9)),
            ("C", (29.35, 26.65, 29.35, 27.85, 28.6, 28.6)),
            ("C", (27.85, 29.35, 26.65, 29.35, 25.9, 28.6)),
            ("L", (21.4, 24.1)),
            ("C", (20.65, 23.35, 20.65, 22.15, 21.4, 21.4)),
            ("Z", ()),
        ),
    ),
)


def svg_paths(*, navigation: bool = False) -> str:
    paths = []
    for color, commands in PATHS:
        data = " ".join(command + " ".join(f"{value:g}" for value in values) for command, values in commands)
        fill = "currentColor" if navigation and color == INK else color
        paths.append(f'  <path class="{PART_NAMES[color]}" fill="{fill}" d="{data}"/>')
    return "\n".join(paths)


def motion_keyframes() -> str:
    picture, tail = [], []
    for offset, dy, shift in MOTION:
        picture.append(f"{offset * 100:g}%{{transform:translateY({dy:g}px)}}")
        tail.append(f"{offset * 100:g}%{{transform:translate({shift:g}px,{shift:g}px)}}")
    return "\n".join(("@keyframes q-picture{" + "".join(picture) + "}", "@keyframes q-tail{" + "".join(tail) + "}"))


def motion_rules(selector: str, seconds: float) -> str:
    return "\n".join(f"{selector} .{part}{{animation:q-{part} {seconds:g}s linear 1;}}" for part in ("picture", "tail"))


def motion_position(progress: float) -> dict[str, tuple[float, float]]:
    for start, end in zip(MOTION, MOTION[1:], strict=False):
        if progress <= end[0]:
            fraction = max(0, (progress - start[0]) / (end[0] - start[0]))
            dy = start[1] + (end[1] - start[1]) * fraction
            shift = start[2] + (end[2] - start[2]) * fraction
            return {"picture": (0, dy), "tail": (shift, shift)}
    return {}


def curve_points(start: tuple[float, float], values: tuple[float, ...]) -> list[tuple[float, float]]:
    points = (start, values[:2], values[2:4], values[4:])
    result = []
    for step in range(1, 25):
        t = step / 24
        weights = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3)
        result.append(
            tuple(sum(point[axis] * weight for point, weight in zip(points, weights, strict=True)) for axis in (0, 1))
        )
    return result


def polygon(commands: tuple) -> list[tuple[float, float]]:
    points = []
    for command, values in commands:
        if command in {"M", "L"}:
            points.append(values)
        elif command == "C":
            points.extend(curve_points(points[-1], values))
    return points


def draw(size: int, background: str | None = None, scale: float = 1.0, progress: float | None = None) -> Image.Image:
    """按 32 单位的画布画，先放大 8 倍再缩小，边缘才平滑。scale < 1 时图案居中缩小、四周留白。"""
    big = size * 8
    img = Image.new("RGBA", (big, big), background or (0, 0, 0, 0))
    pen = ImageDraw.Draw(img)
    unit = big / 32 * scale
    center = big / 2
    offsets = motion_position(progress) if progress is not None else {}

    for color, commands in PATHS:
        dx, dy = offsets.get(PART_NAMES[color], (0, 0))
        points = [(center + (x + dx - 16) * unit, center + (y + dy - 16) * unit) for x, y in polygon(commands)]
        pen.polygon(points, fill=color)
    return img.resize((size, size), Image.LANCZOS)


def write_motion_assets(paths: str) -> None:
    keyframes = motion_keyframes()
    svg_style = (
        keyframes + "\n@media (prefers-reduced-motion:no-preference){\n" + motion_rules("svg", MOTION_SECONDS) + "\n}"
    )
    (ROOT / "docs" / "logo-animated.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" '
        'viewBox="0 0 32 32" role="img" aria-label="求原图">\n'
        f"<style>\n{svg_style}\n</style>\n{paths}\n</svg>\n",
        encoding="utf-8",
    )
    css = (
        "/* Generated by scripts/make_icons.py. */\n" + keyframes + "\n"
        "@media (prefers-reduced-motion:no-preference){\n" + motion_rules(".brand:focus-visible", 1.2) + "\n"
        "@media (hover:hover){\n" + motion_rules(".brand:hover", 1.2) + "\n}\n"
        "@media (hover:none){\n" + motion_rules(".brand", MOTION_SECONDS) + "\n}\n}\n"
    )
    (STATIC / "brand-motion.css").write_text(css, encoding="utf-8")
    write_readme_gif()


def write_readme_gif() -> None:
    # GIF 不支持 SVG 动画或透明半色调；统一暖底与调色板，避免缩放后边缘闪烁。
    frames = [draw(144, background="#f5f1ea", progress=step / 59).convert("RGB") for step in range(60)]
    palette = frames[0].convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
    frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    # 不写 loop 扩展，只播放一轮；减少动态效果时由 README 的 picture 改用静态 SVG。
    frames[0].save(
        ROOT / "docs" / "logo-animated.gif",
        save_all=True,
        append_images=frames[1:],
        duration=round(MOTION_SECONDS * 1000 / len(frames)),
        optimize=False,
        disposal=1,
    )


def main() -> None:
    paths = svg_paths()
    write_motion_assets(paths)
    # 暗色标签栏自动换浅色外框，图片与 Q 尾笔保留品牌色。
    dark = f"<style>@media (prefers-color-scheme:dark){{.frame{{fill:{PAPER}}}}}</style>"
    (STATIC / "favicon.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">\n{dark}\n{paths}\n</svg>\n',
        encoding="utf-8",
    )
    (ROOT / "docs" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" '
        f'viewBox="0 0 32 32" role="img" aria-label="求原图">\n{paths}\n</svg>\n',
        encoding="utf-8",
    )
    (STATIC.parent / "templates" / "_brand_icon.html").write_text(
        "{# Generated by scripts/make_icons.py; shared with favicon and README. #}\n"
        f'<svg viewBox="0 0 32 32" aria-hidden="true">\n{svg_paths(navigation=True)}\n</svg>\n',
        encoding="utf-8",
    )
    # 百度搜索结果的站点图标只读 /favicon.ico；Google 要 48 的倍数
    draw(48).save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    draw(180, background=PAPER, scale=0.72).save(STATIC / "apple-touch-icon.png", optimize=True)


if __name__ == "__main__":
    main()
