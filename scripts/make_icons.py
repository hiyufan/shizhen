"""生成站点图标：favicon.ico / favicon.svg / apple-touch-icon.png，写到 static/ 下。

图案就是页面上一直用的那个：深色圆环 + 天蓝圆点（32×32 画布，环半径 13、线宽 2.5，点半径 6）。
改了图案重新跑一遍：uv run --no-project --with pillow python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path(__file__).resolve().parent.parent / "src" / "parse_video_py" / "static"
INK = "#1a1815"
SKY = "#6BAFDF"
PAPER = "#fafaf9"  # 站点底色（site.css 的 --cold）；iOS 主屏图标不认透明，要垫底色

# 暗色标签栏上深色圆环看不见，跟着系统配色换成浅色
SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<style>.ring{{stroke:{INK}}}@media (prefers-color-scheme:dark){{.ring{{stroke:{PAPER}}}}}</style>
<circle class="ring" cx="16" cy="16" r="13" fill="none" stroke-width="2.5"/>
<circle cx="16" cy="16" r="6" fill="{SKY}"/>
</svg>
"""


def draw(size: int, background: str | None = None, scale: float = 1.0) -> Image.Image:
    """按 32 单位的画布画，先放大 8 倍再缩小，边缘才平滑。scale < 1 时图案居中缩小、四周留白。"""
    big = size * 8
    img = Image.new("RGBA", (big, big), background or (0, 0, 0, 0))
    pen = ImageDraw.Draw(img)
    unit = big / 32 * scale
    center = big / 2

    def circle(radius: float, **style) -> None:
        r = radius * unit
        pen.ellipse((center - r, center - r, center + r, center + r), **style)

    # 圆环：线宽 2.5 以 r=13 为中线，外沿 14.25、内沿 11.75
    circle(13 + 1.25, outline=INK, width=round(2.5 * unit))
    circle(6, fill=SKY)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    (STATIC / "favicon.svg").write_text(SVG, encoding="utf-8")
    # 百度搜索结果的站点图标只读 /favicon.ico；Google 要 48 的倍数
    draw(48).save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    draw(180, background=PAPER, scale=0.72).save(STATIC / "apple-touch-icon.png", optimize=True)


if __name__ == "__main__":
    main()
