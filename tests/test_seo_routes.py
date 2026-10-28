"""给搜索引擎看的那些细节：HEAD、站点图标、结尾斜杠的 301、旧博客地址的 410。"""

import pytest
from fastapi.testclient import TestClient

from qiuyuantu import web


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
    from qiuyuantu import stats

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
    from qiuyuantu.web.rendering import static_url

    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(content_type) and r.content
    versioned = static_url(path.removeprefix("/"))
    assert f'href="{versioned}"' in client.get("/").text
    assert client.get(versioned).content == r.content


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
    from qiuyuantu.guides import GUIDE_BY_SLUG, GUIDES
    from qiuyuantu.seo import PAGES

    assert all(r in GUIDE_BY_SLUG for g in GUIDES for r in g.related)
    assert all(r in GUIDE_BY_SLUG for p in PAGES for r in p.guides)
    sitemap = client.get("/sitemap.xml").text
    for g in GUIDES:
        html = client.get(g.path).text
        assert g.h1 in html and g.path in sitemap
        # 数据表整段输出，不能被包进 <p> 里（<p><div> 是非法嵌套，浏览器会拆开）
        assert "<p><div" not in html


def test_guide_table_marks_number_cells():
    from qiuyuantu.guides import table

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


def test_private_and_missing_pages_are_noindex(client, monkeypatch):
    from urllib.robotparser import RobotFileParser

    from lxml import html

    from qiuyuantu import stats

    assert "noindex" in client.get("/no-such-page").text
    monkeypatch.setattr(stats, "TOKEN", "test-mode-token")
    robots = RobotFileParser()
    robots.parse(client.get("/robots.txt").text.splitlines())
    for agent in ("Googlebot", "bingbot"):
        assert robots.can_fetch(agent, "http://testserver/test")
        assert not robots.can_fetch(agent, "http://testserver/stats")
        assert not robots.can_fetch(agent, "http://testserver/api/stats")
    response = client.get("/test")
    assert response.status_code == 200
    assert response.headers["X-Robots-Tag"] == "noindex"
    assert response.headers["Cache-Control"] == "no-store"
    assert html.fromstring(response.text).xpath('//meta[@name="robots"]/@content') == ["noindex,nofollow"]
    assert "/test</loc>" not in client.get("/sitemap.xml").text


def test_sitemap_page_revision_does_not_redate_other_pages(client, monkeypatch):
    from dataclasses import replace
    from xml.etree import ElementTree

    from qiuyuantu import seo

    monkeypatch.setattr(
        seo, "PAGES", [replace(page, updated="2001-02-03") if page.slug == "bilibili" else page for page in seo.PAGES]
    )
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    tree = ElementTree.fromstring(client.get("/sitemap.xml").content)
    dates = {node.findtext("sm:loc", namespaces=ns): node.findtext("sm:lastmod", namespaces=ns) for node in tree}
    assert dates["http://testserver/bilibili"] == "2001-02-03"
    assert dates["http://testserver/"] == seo.PAGE_BY_SLUG[""].updated


@pytest.mark.parametrize("slug", ["qq-music-cover", "netease-music-cover"])
def test_music_search_entry_has_matching_metadata_and_crawlable_tool_links(client, slug):
    import json

    from lxml import html

    from qiuyuantu import seo

    page = seo.PAGE_BY_SLUG[slug]
    response = client.get(page.path)
    assert response.status_code == 200
    doc = html.fromstring(response.text)
    assert doc.xpath("//title/text()") == [page.title]
    assert doc.xpath('//meta[@name="description"]/@content') == [page.description]
    assert doc.xpath('//link[@rel="canonical"]/@href') == ["http://testserver" + page.path]
    assert doc.xpath('//form[@id="paste-form"]//textarea[@id="url"]/@placeholder') == [page.placeholder]
    graph = json.loads(doc.xpath('//script[@type="application/ld+json"]/text()')[0])
    faq = next(node for node in graph if node["@type"] == "FAQPage")
    assert [node["name"] for node in faq["mainEntity"]] == [q for q, _ in page.all_faq]
    for referring in ["/", "/guide/music-cover-download"]:
        assert page.path in html.fromstring(client.get(referring).text).xpath("//a/@href")
    assert page.path in client.get("/sitemap.xml").text
