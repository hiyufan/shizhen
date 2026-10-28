"""横幅隔离，MultiTag 在公开页头部执行，管理页不加载广告。"""

import re

import pytest
from fastapi.testclient import TestClient
from lxml import html

from qiuyuantu import stats, web
from qiuyuantu.web import ads


@pytest.mark.parametrize("placement,width,height", [("desktop", 728, 90), ("mobile", 320, 50)])
def test_banner_document_is_isolated_and_not_counted_as_a_page(monkeypatch, placement, width, height):
    recorded = []
    monkeypatch.setattr(stats, "TOKEN", "test-token")
    monkeypatch.setattr(stats, "record", lambda *args, **kwargs: recorded.append(args))
    client = TestClient(web.app)
    response = client.get(f"/api/ads/{placement}")
    assert response.status_code == 200
    assert f'data-width="{width}" data-height="{height}"' in response.text
    assert 'src="https://bauval.org/22/' in response.text
    assert not re.search(r"<script(?:\s[^>]*)?>\s*[^<\s]", response.text)
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    assert recorded == []
    page = client.get("/")
    assert "bauval.org" not in page.text
    assert "bauval.org" not in page.headers["content-security-policy"]
    assert 'data-banner="/api/ads/"' in page.text
    assert "data-banner" not in client.get("/guide/video-to-gif").text


def test_ads_can_be_disabled_and_unknown_placements_never_load_scripts(monkeypatch):
    client = TestClient(web.app)
    assert client.get("/api/ads/unknown").status_code == 404
    monkeypatch.setattr(ads, "ENABLED", False)
    assert client.get("/api/ads/mobile").status_code == 404
    assert 'data-banner="/api/ads/"' not in client.get("/").text
    assert "quge5.com" not in client.get("/").text
    assert "script-src 'self';" in client.get("/").headers["content-security-policy"]


def test_multitag_runs_once_in_public_page_head_and_not_in_banner_documents():
    client = TestClient(web.app)
    for placement in ("desktop", "mobile"):
        response = client.get(f"/api/ads/{placement}")
        assert "quge5.com" not in response.text
        assert "quge5.com" not in response.headers["content-security-policy"]
    for path in ("/", "/douyin", "/guides", "/guide/video-to-gif"):
        response = client.get(path)
        document = html.fromstring(response.text)
        scripts = document.xpath('//script[@src="https://quge5.com/88/tag.min.js"]')
        assert len(scripts) == 1
        assert scripts[0] is document.xpath("//head/*")[0]
        assert scripts[0].attrib == {
            "src": "https://quge5.com/88/tag.min.js",
            "data-zone": "302981",
            "async": "",
            "data-cfasync": "false",
        }
        csp = response.headers["content-security-policy"]
        assert all(origin in csp for origin in ads.MULTITAG_SCRIPT_ORIGINS)
        assert "worker-src 'self'" in csp


def test_private_and_missing_pages_do_not_load_multitag(monkeypatch):
    monkeypatch.setattr(stats, "TOKEN", "ad-private-test")
    client = TestClient(web.app)
    for path, status in (
        ("/stats?token=ad-private-test", 200),
        ("/test", 200),
        ("/does-not-exist", 404),
        ("/about", 410),
    ):
        response = client.get(path)
        assert response.status_code == status
        assert "quge5.com" not in response.text
        assert "quge5.com" not in response.headers.get("content-security-policy", "")
