"""给搜索引擎看的那些细节：HEAD、站点图标、结尾斜杠的 301、旧博客地址的 410。"""

import pytest
from fastapi.testclient import TestClient

from parse_video_py import web


@pytest.fixture
def client():
    return TestClient(web.app, follow_redirects=False)


@pytest.mark.parametrize(
    "path", ["/", "/douyin", "/guides", "/guide/douyin-no-watermark", "/robots.txt", "/sitemap.xml"]
)
def test_pages_answer_head(client, path):
    get, head = client.get(path), client.head(path)
    assert head.status_code == get.status_code == 200
    assert head.headers["content-type"] == get.headers["content-type"]
    assert head.content == b""


def test_head_does_not_count_as_a_page_view(client, monkeypatch):
    from parse_video_py import stats

    seen = []
    monkeypatch.setattr(stats, "enabled", lambda: True)
    monkeypatch.setattr(stats, "record", lambda kind, ip, **kw: seen.append(kind))
    monkeypatch.setattr(stats, "ignored_ip", lambda ip: False)
    client.head("/douyin", headers={"user-agent": "Mozilla/5.0"})
    assert seen == []
    client.get("/douyin", headers={"user-agent": "Mozilla/5.0"})
    assert seen == ["view"]


@pytest.mark.parametrize(
    ("path", "content_type"),
    [("/favicon.ico", "image/x-icon"), ("/favicon.svg", "image/svg+xml"), ("/apple-touch-icon.png", "image/png")],
)
def test_root_icons(client, path, content_type):
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(content_type) and r.content
    assert f'href="{path}"' in client.get("/").text


@pytest.mark.parametrize(
    ("path", "target"),
    [("/douyin/", "/douyin"), ("/guide/video-to-gif/", "/guide/video-to-gif"), ("/gif/?a=1", "/gif?a=1")],
)
def test_trailing_slash_is_a_permanent_redirect(client, path, target):
    r = client.get(path)
    assert r.status_code == 301 and r.headers["location"] == target


def test_trailing_slash_redirect_stays_on_site(client):
    # //evil.com/ 不能跳成 //evil.com：浏览器会当成去别的网站
    r = client.get("http://testserver//evil.com/")
    assert r.status_code == 301 and r.headers["location"] == "/evil.com"


@pytest.mark.parametrize(
    "path", ["/about", "/tags/python", "/upload/IMG_1.jpg", "/themes/theme-akari/a.png", "/rss.xml"]
)
def test_old_blog_urls_are_gone(client, path):
    r = client.get(path)
    assert r.status_code == 410 and "text/html" in r.headers["content-type"]


def test_unknown_pages_are_still_404(client):
    assert client.get("/no-such-page").status_code == 404
    assert client.get("/guide/no-such-guide").status_code == 404
