"""前端模块：带版本号的路径、import 图、缓存头。"""

import re

import pytest
from fastapi.testclient import TestClient

from parse_video_py import web
from parse_video_py.web import rendering


@pytest.fixture
def client():
    return TestClient(web.app)


def test_every_relative_import_exists():
    for path in rendering.JS_DIR.glob("*.js"):
        for dep in re.findall(r"from\s+'\./([\w-]+\.js)'", path.read_text(encoding="utf-8")):
            assert (rendering.JS_DIR / dep).is_file(), f"{path.name} imports missing {dep}"


def test_tool_page_preloads_the_whole_import_graph(client):
    html = client.get("/").text
    version = rendering.js_version()
    assert f'<script type="module" src="/js/{version}/app.js">' in html
    assert f'<script type="module" src="/js/{version}/site.js">' in html
    preloaded = set(re.findall(r'<link rel="modulepreload" href="/js/\w+/([\w-]+\.js)">', html))
    assert {"api.js", "converter.js", "dom.js", "result.js", "save.js", "trimmer.js", "ui.js"} <= preloaded
    assert "app.js" not in preloaded
    assert "<script>" not in html  # 不再有内联脚本


def test_other_pages_only_load_site_effects(client):
    html = client.get("/guide/video-to-gif").text
    assert "site.js" in html and "app.js" not in html and "modulepreload" not in html
    assert 'href="/#upload"' in html  # 只有工具页能接住上传


def test_current_version_is_cached_forever_stale_one_is_not(client):
    current = client.get(rendering.js_url("dom.js"))
    assert current.status_code == 200 and current.headers["content-type"].startswith("text/javascript")
    assert "immutable" in current.headers["cache-control"]
    stale = client.get("/js/0000000000/dom.js")
    assert stale.status_code == 200 and stale.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("name", ["nope.js", "..%2Fsite.css", "app.ts"])
def test_unknown_modules_404(client, name):
    assert client.get(f"/js/{rendering.js_version()}/{name}").status_code == 404
