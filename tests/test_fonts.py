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
