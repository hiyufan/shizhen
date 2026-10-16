"""重新生成 Noto Serif SC 子集：改了站内文案（模板 / seo.py / guides.py）后跑一次：
    uv run --no-project --with-editable '.[web,dev]' python scripts/subset_fonts.py --src <OTF 目录>

需要 Noto Serif CJK SC 的 Bold / Black OTF（https://github.com/notofonts/noto-cjk/tree/main/Serif/OTF/SimplifiedChinese）
放在 fonts/ 目录或用 --src 指定。没有 OTF 时拿现有的 woff2 当源：只能删字不能加字，缺字会报出来。

两个字重收的字不一样：
- 700（h2 / h3 / 常见问题的标题，还有脚本里拼出来的界面文字）：模板 + seo.py + guides.py + status.py 里出现的所有字。
  切成两片（2026-10-14）：首页和各平台落地页这些入口页标题用到的字放 noto-serif-sc-700.woff2（预加载），
  其余放 noto-serif-sc-700-more.woff2。fonts.css 里两片各写 unicode-range，浏览器只在页面上真有
  B 片的字时才去下载它：首页原来要下 247KB（收了 745 个字，首页只用 92 个），从国内打开首页
  一共才传 450KB，跨境下载量就是快慢。
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
        os.path.join(PKG, "status.py"),
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


ENTRY_HEADINGS = "h1, h2, h3, .faq summary"  # site.css 里用 700 字重衬线体的地方


def entry_text() -> str:
    """首页和各平台落地页（搜索进来的入口）标题里的字，渲染页面现取。"""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from fastapi.testclient import TestClient
    from lxml import html as lxml_html

    from parse_video_py import seo, web

    client = TestClient(web.app)
    text = ""
    for page in seo.PAGES:
        doc = lxml_html.fromstring(client.get(page.path).text)
        text += "".join(el.text_content() for el in doc.cssselect(ENTRY_HEADINGS))
    return text


def unicode_range(chars: set[str]) -> str:
    """{'一', '丁', '七'} -> 'U+4E00-4E01, U+4E03'。"""
    points = sorted(ord(ch) for ch in chars)
    ranges: list[list[int]] = []
    for p in points:
        if ranges and p == ranges[-1][1] + 1:
            ranges[-1][1] = p
        else:
            ranges.append([p, p])
    return ", ".join(f"U+{a:X}" if a == b else f"U+{a:X}-{b:X}" for a, b in ranges)


def _subset(src: str, chars: set[str], out: str) -> None:
    glyphs = out + ".txt"
    Path(glyphs).write_text("".join(sorted(chars)), encoding="utf-8")
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
    os.remove(glyphs)


_FACE_700 = re.compile(r"/\* 700-START[^*]*\*/.*?/\* 700-END \*/", re.S)


def _write_700_faces(entry: set[str], more: set[str]) -> None:
    """fonts.css 里两片 700 的 @font-face（START / END 标记之间整段重写）。"""
    face = (
        "@font-face {{ font-family: 'Noto Serif SC'; font-style: normal; font-weight: 700; font-display: swap; "
        "src: url(/static/{file}) format('woff2'); unicode-range: {ranges}; }}"
    )
    block = "\n".join(
        [
            "/* 700-START 由 scripts/subset_fonts.py 生成，别手改 */",
            face.format(file="noto-serif-sc-700.woff2", ranges=unicode_range(entry)),
            face.format(file="noto-serif-sc-700-more.woff2", ranges=unicode_range(more)),
            "/* 700-END */",
        ]
    )
    css_path = Path(PKG, "static", "fonts.css")
    css = css_path.read_text(encoding="utf-8")
    if not _FACE_700.search(css):
        raise SystemExit("fonts.css 里找不到 /* 700-START */ … /* 700-END */ 标记")
    css_path.write_text(_FACE_700.sub(lambda _m: block, css), encoding="utf-8")


def build_700(src: str) -> None:
    from fontTools.ttLib import TTFont

    chars = {ch for ch in page_text() if ord(ch) > 0x2000} | _ALWAYS
    # 入口页用到的字 + ASCII / 标点放 A 片；只在教程、状态页这些页面出现的放 B 片。两片不重叠
    entry = ({ch for ch in entry_text() if ch in chars} | _ALWAYS) & chars
    more = chars - entry
    out_a = os.path.join(PKG, "static", "noto-serif-sc-700.woff2")
    out_b = os.path.join(PKG, "static", "noto-serif-sc-700-more.woff2")
    _subset(src, entry, out_a)
    _subset(src, more, out_b)
    # unicode-range 按字体里实际有的字写：子集化时连字规则会顺带收进几个没要的字（比如 ≥），
    # 按要求的字写的话，这些字在 CSS 里没登记，两边就对不上
    cmap_a, cmap_b = set(TTFont(out_a).getBestCmap()), set(TTFont(out_b).getBestCmap())
    _write_700_faces({chr(c) for c in cmap_a}, {chr(c) for c in cmap_b - cmap_a})
    got = cmap_a | cmap_b
    missing = "".join(sorted(ch for ch in chars if ord(ch) > 0x2000 and ord(ch) not in got))
    print(
        f"700: 入口页 {len(entry)} 个字形 {os.path.getsize(out_a) // 1000} KB，"
        f"其余 {len(more)} 个 {os.path.getsize(out_b) // 1000} KB" + (f"，源字体里没有：{missing}" if missing else "")
    )


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
        if weight == "700":
            src = os.path.join(args.src, sources["700"][0])
            if not os.path.exists(src):
                raise SystemExit(f"没有 {src}：700 要切两片，得用原始 OTF（两片合起来才是全部的字）")
            build_700(src)
            continue
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
