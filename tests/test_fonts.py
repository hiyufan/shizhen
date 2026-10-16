"""900 字重只收了各页大标题和品牌名的字（scripts/subset_fonts.py）：新加的标题用了没收的字，
那个字会掉回系统字体、和旁边的字粗细不一样。改了标题就重跑那个脚本。"""

import importlib.util
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("subset_fonts", ROOT / "scripts" / "subset_fonts.py")
subset_fonts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(subset_fonts)


def test_heading_font_covers_every_h1():
    text = subset_fonts.heading_text()
    assert "求原图" in text and "谁在什么时候用" in text  # 品牌名、模板里写死的 h1 都收到了
    cmap = TTFont(ROOT / "src/parse_video_py/static/noto-serif-sc-900.woff2").getBestCmap()
    missing = sorted({ch for ch in text if ch.strip() and ord(ch) not in cmap})
    assert not missing, f"900 字重缺字 {''.join(missing)}，跑一下 scripts/subset_fonts.py --weight 900"


def test_heading_font_stays_small():
    # 它是首页 LCP 元素的字体，Lighthouse 把它的下载算进 LCP；整套常用字 200KB 会让 LCP 多出近 1 秒
    assert (ROOT / "src/parse_video_py/static/noto-serif-sc-900.woff2").stat().st_size < 80_000


def test_font_urls_are_versioned_and_preloads_match_font_faces():
    import re

    from fastapi.testclient import TestClient

    from parse_video_py import web

    html = TestClient(web.app).get("/").text
    preloads = set(re.findall(r'<link rel="preload" href="([^"]+\.woff2[^"]*)"', html))
    faces = set(re.findall(r"url\((/static/[^)]+\.woff2[^)]*)\)", html))
    assert len(preloads) == 5
    # 不一致浏览器会当成两个资源，预加载白做；不带版本，重裁字体后 nginx / 浏览器缓存会一直发旧的
    assert preloads <= faces
    assert all(re.search(r"\.woff2\?v=[0-9a-f]{8}$", u) for u in faces)


def test_700_slices_cover_everything_and_entry_pages_need_only_the_first():
    """700 字重切成两片（入口页的字 / 其余），靠 unicode-range 按需下载：两片不能重叠、合起来不能缺字；
    入口页标题的字都得在第一片，不然首页又会去下第二片（188KB）。改了落地页标题就重跑脚本。"""
    first = set(TTFont(ROOT / "src/parse_video_py/static/noto-serif-sc-700.woff2").getBestCmap())
    more = set(TTFont(ROOT / "src/parse_video_py/static/noto-serif-sc-700-more.woff2").getBestCmap())
    assert not ({c for c in first & more if c > 0x2000})  # 只在一片里，浏览器才不会两片都下
    site = {ord(ch) for ch in subset_fonts.page_text() if ord(ch) > 0x2000} - {ord("▾")}  # ▾ 源字体里本来就没有
    assert not site - first - more, "700 字重缺字，跑一下 scripts/subset_fonts.py --weight 700"
    # fonts.css 里每片的 unicode-range 要盖住它收的字，不然浏览器不会为这些字去下载这一片（2026-10-16：脚本认不出
    # 自己写的带说明的标记，字体文件更新了、CSS 没改，新收的「警续垮」会掉回系统字体）
    import re

    css = (ROOT / "src/parse_video_py/static/fonts.css").read_text(encoding="utf-8")
    for name, cmap in (("noto-serif-sc-700.woff2", first), ("noto-serif-sc-700-more.woff2", more)):
        ranges = re.search(re.escape(name) + r"\) format\('woff2'\); unicode-range: ([^;]+);", css).group(1)
        covered = set()
        for part in ranges.split(", "):
            lo, _, hi = part[2:].partition("-")
            covered |= set(range(int(lo, 16), int(hi or lo, 16) + 1))
        missing = "".join(chr(c) for c in sorted({c for c in cmap if c > 0x2000} - covered))
        assert not missing, (
            f"{name} 收了 {missing}，fonts.css 的 unicode-range 没写：跑一下 scripts/subset_fonts.py --weight 700"
        )
    entry = {ord(ch) for ch in subset_fonts.entry_text() if ord(ch) > 0x2000} & (first | more)
    missing = "".join(sorted(chr(c) for c in entry - first))
    assert not missing, (
        f"入口页标题的 {missing} 在第二片里，首页会多下 188KB：跑一下 scripts/subset_fonts.py --weight 700"
    )
