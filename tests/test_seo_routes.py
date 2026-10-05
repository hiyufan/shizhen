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


def test_every_guide_renders_and_links_resolve(client):
    from parse_video_py.guides import GUIDE_BY_SLUG, GUIDES
    from parse_video_py.seo import PAGES

    assert all(r in GUIDE_BY_SLUG for g in GUIDES for r in g.related)
    assert all(r in GUIDE_BY_SLUG for p in PAGES for r in p.guides)
    sitemap = client.get("/sitemap.xml").text
    for g in GUIDES:
        html = client.get(g.path).text
        assert g.h1 in html and g.path in sitemap
        # 数据表整段输出，不能被包进 <p> 里（<p><div> 是非法嵌套，浏览器会拆开）
        assert "<p><div" not in html


def test_guide_table_marks_number_cells():
    from parse_video_py.guides import table

    html = table(["时长", "大小"], [["3 秒", "0.4 MB"]], "2026-10-04 实测")
    assert '<td class="n">3 秒</td>' in html and '<p class="note">2026-10-04 实测</p>' in html


def test_indexed_pages_have_descriptive_titles_and_no_private_links(client):
    """Bing 站点扫描会报「标题太短」：中文标题按字数算，二十来个字就被判短。站长页（/test）
    以前每页都带一个链接，被当成正文太少的页面抓了，现在 HTML 里不出现它的地址。"""
    import re
    from urllib.parse import urlparse

    for loc in re.findall(r"<loc>([^<]+)</loc>", client.get("/sitemap.xml").text):
        path = urlparse(loc).path
        html = client.get(path).text
        title = re.search(r"<title>([^<]*)</title>", html).group(1)
        assert len(title) >= 28, (path, title)
        assert 'href="/test"' not in html
        assert html.count('name="robots"') == 1


def test_private_and_missing_pages_are_noindex(client):
    assert "noindex" in client.get("/no-such-page").text
    assert "Disallow: /test" in client.get("/robots.txt").text
