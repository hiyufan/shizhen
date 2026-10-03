"""重新生成 Noto Serif SC 子集：改了站内文案（模板 / seo.py / guides.py）后跑一次：
    uv run --no-project --with-editable '.[web,dev]' python scripts/subset_fonts.py --src <OTF 目录>

需要 Noto Serif CJK SC 的 Bold / Black OTF（https://github.com/notofonts/noto-cjk/tree/main/Serif/OTF/SimplifiedChinese）
放在 fonts/ 目录或用 --src 指定。没有 OTF 时拿现有的 woff2 当源：只能删字不能加字，缺字会报出来。

两个字重收的字不一样：
- 700（h2 / h3 / 卡片标题，还有脚本里拼出来的界面文字）：模板 + seo.py + guides.py 里出现的所有字。
- 900 只用在品牌名和各页大标题（.brand、.hero h1、.article h1）：只收这些字。它是首屏标题的字体、
  首页 LCP 元素就是它，Lighthouse 会把它的下载算进 LCP；全收是 200KB，只收标题 30KB 左右。
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = os.path.join(os.path.dirname(__file__), "..")
PKG = os.path.join(ROOT, "src", "parse_video_py")
BRAND = "求原图"
# 标点和 ASCII 一律带上，标题里随手加个符号不至于掉字
_ALWAYS = set("0123456789+-–—·…（）()[]{}“”‘’、。，：；！？%×") | {chr(c) for c in range(0x20, 0x7F)}


def page_text() -> str:
    text = ""
    for f in glob.glob(os.path.join(PKG, "templates", "*.html")) + [
        os.path.join(PKG, "seo.py"),
        os.path.join(PKG, "guides.py"),
    ]:
        text += Path(f).read_text(encoding="utf-8")
    return text


def heading_text() -> str:
    """所有页面 h1 的文字 + 品牌名，即全站用到 900 字重的文字。"""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from parse_video_py import guides, seo

    text = BRAND + "".join(p.h1 for p in seo.PAGES) + "".join(g.h1 for g in guides.GUIDES)
    for f in glob.glob(os.path.join(PKG, "templates", "*.html")):  # 写死在模板里的 h1（教程列表、404、统计页）
        text += "".join(re.findall(r"<h1[^>]*>([^{<]*)</h1>", Path(f).read_text(encoding="utf-8")))
    return re.sub(r"<[^>]+>", "", text)  # page.h1 里允许 <em> / <br>


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src", default=os.path.join(ROOT, "fonts"), help="存放 NotoSerifCJKsc-Bold.otf / -Black.otf 的目录"
    )
    ap.add_argument("--weight", choices=("700", "900"), action="append", help="只重建这个字重（可重复），默认两个都建")
    args = ap.parse_args()

    from fontTools.ttLib import TTFont

    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    sources = {"700": ("NotoSerifCJKsc-Bold.otf", page_text), "900": ("NotoSerifCJKsc-Black.otf", heading_text)}
    for weight in args.weight or sources:
        name, text = sources[weight][0], sources[weight][1]()
        chars = {ch for ch in text if ord(ch) > 0x2000} | _ALWAYS
        glyphs = os.path.join(ROOT, "data", f"glyphs-{weight}.txt")
        Path(glyphs).write_text("".join(sorted(chars)), encoding="utf-8")
        out = os.path.join(PKG, "static", f"noto-serif-sc-{weight}.woff2")
        src = os.path.join(args.src, name)
        if not os.path.exists(src):
            print(f"没有 {src}，用现有的 {os.path.basename(out)} 当源（只能删字）")
            src = out
        subprocess.run(
            [
                sys.executable,
                "-m",
                "fontTools.subset",
                src,
                f"--text-file={glyphs}",
                "--flavor=woff2",
                f"--output-file={out}.tmp",
                "--layout-features=kern,liga,locl",
                "--no-hinting",
                "--desubroutinize",
            ],
            check=True,
        )
        os.replace(out + ".tmp", out)
        cmap = TTFont(out).getBestCmap()
        missing = "".join(sorted(ch for ch in chars if ord(ch) > 0x2000 and ord(ch) not in cmap))
        print(
            f"{weight}: {len(chars)} 个字形，{os.path.getsize(out) // 1000} KB"
            + (f"，源字体里没有：{missing}" if missing else "")
        )


if __name__ == "__main__":
    main()
