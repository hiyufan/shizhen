"""页面偏好：浏览器协商、手动覆盖、缓存隔离与完整的公共英文页面。"""

import dataclasses
import json
import re

import pytest
from fastapi.testclient import TestClient
from lxml import html

from qiuyuantu import guides, seo, web
from qiuyuantu.web.preferences import browser_language, english


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("", "zh"),
        ("zh-TW,zh;q=0.9,en;q=0.8", "zh"),
        ("en-GB,en;q=0.9,zh;q=0.5", "en"),
        ("zh;q=0.3,en-US;q=0.9", "en"),
        ("fr-FR,zh-CN;q=0.7", "zh"),
        ("fr-FR", "en"),
        ("en;q=0,zh;q=0.8", "zh"),
        ("en;q=invalid,zh", "zh"),
    ],
)
def test_browser_language_quality_and_fallback(header, expected):
    assert browser_language(header) == expected


def test_preferences_are_request_scoped_and_private(monkeypatch):
    async def no_warmup():
        return None

    # 页面偏好不依赖外部平台；安装可选浏览器依赖后仍保持此测试离线。
    from qiuyuantu.parser import douyin

    monkeypatch.setattr(douyin, "warmup_browser", no_warmup)
    with TestClient(web.app) as client:
        response = client.get("/", headers={"Accept-Language": "en-US"})
        doc = html.fromstring(response.text)
        assert doc.get("lang") == "en"
        assert doc.get("data-theme") == "auto"
        assert doc.xpath("//h1")[0].text_content() == "Keep the moment.Videos, originals & GIFs."
        assert response.headers["content-language"] == "en"
        assert response.headers["cache-control"] == "private, max-age=600"
        assert {"Accept-Language", "Cookie"} <= set(response.headers["vary"].split(", "))

        client.cookies.set("qyt_language", "zh")
        client.cookies.set("qyt_theme", "dark")
        response = client.get("/", headers={"Accept-Language": "en-US"})
        doc = html.fromstring(response.text)
        assert doc.get("lang") == "zh-CN"
        assert doc.get("data-theme") == "dark"
        assert "把视频定格成" in doc.xpath("//h1")[0].text_content()
        assert doc.xpath('//input[@name="theme"][@value="dark"][@checked]')

        client.cookies.set("qyt_language", "auto")
        client.cookies.set("qyt_theme", "invalid")
        doc = html.fromstring(client.get("/", headers={"Accept-Language": "en"}).text)
        assert doc.get("lang") == "en"
        assert doc.get("data-theme") == "auto"


@pytest.mark.parametrize("path", [p.path for p in seo.PAGES] + [g.path for g in guides.GUIDES] + ["/guides", "/status"])
def test_public_english_content_and_metadata_are_translated(path):
    client = TestClient(web.app)
    response = client.get(path, headers={"Accept-Language": "en"})
    assert response.status_code == 200
    doc = html.fromstring(response.text)
    for element in doc.xpath("//section | //article | //title | //meta[@name='description']/@content"):
        text = element if isinstance(element, str) else element.text_content()
        assert not re.search(r"[\u3400-\u9fff]", text), (path, text)
    assert doc.xpath('//meta[@property="og:locale"]/@content') == ["en_US"]
    assert doc.xpath('//link[@rel="canonical"]/@href')[0].endswith(path)
    structured = doc.xpath('//script[@type="application/ld+json"]/text()')[0]
    assert not re.search(r"[\u3400-\u9fff]", structured)
    assert '"zh-CN"' not in structured
    assert json.loads(structured)


def _content_strings(value):
    if dataclasses.is_dataclass(value):
        yield from _content_strings(dataclasses.asdict(value))
    elif isinstance(value, dict):
        for item in value.values():
            yield from _content_strings(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _content_strings(item)
    elif isinstance(value, str) and re.search(r"[\u3400-\u9fff]", value):
        yield value


def test_catalog_covers_all_platform_and_guide_content():
    content = [seo.PAGES, seo.COMMON_FAQ, guides.GUIDES]
    assert set(_content_strings(content)) <= english().keys()


def test_invalid_language_cookie_and_english_not_found():
    client = TestClient(web.app)
    client.cookies.set("qyt_language", "invalid")
    response = client.get("/missing-page", headers={"Accept-Language": "en"})
    assert response.status_code == 404
    doc = html.fromstring(response.text)
    assert doc.get("lang") == "en"
    assert doc.xpath("//h1")[0].text_content() == "This page is missing"


def test_english_messages_are_a_cached_module_not_inlined():
    from qiuyuantu.web import rendering
    from qiuyuantu.web.preferences import ui_english

    client = TestClient(web.app)
    module = rendering.js_url("messages-en.js")
    english_page = client.get("/guide/video-to-gif", headers={"Accept-Language": "en"}).text
    assert f'<link rel="modulepreload" href="{module}">' in english_page
    assert 'id="translations"' not in english_page
    assert "messages-en.js" not in client.get("/", headers={"Accept-Language": "zh-CN"}).text

    response = client.get(module)
    assert response.headers["content-type"].startswith("text/javascript")
    assert "immutable" in response.headers["cache-control"]
    body = response.text
    assert body.startswith("export default ") and json.loads(body[len("export default ") : -2]) == ui_english()
