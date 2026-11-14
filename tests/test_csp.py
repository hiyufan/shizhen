"""主页面禁止内联及广告脚本；普通横幅仅在独立的受限文档执行。"""

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from qiuyuantu import web
from qiuyuantu.web.middleware import build_csp


def _directives(csp):
    return {d.split()[0]: d.split()[1:] for d in csp.split("; ")}


def test_pages_forbid_inline_scripts():
    csp = TestClient(web.app).get("/").headers["content-security-policy"]
    assert _directives(csp)["script-src"] == ["'self'"]
    assert "'unsafe-inline'" not in _directives(csp)["script-src"]


def test_analytics_snippet_is_allowed_by_hash_and_origin():
    body = "var _hmt = _hmt || [];"
    snippet = f'<script>{body}</script><script async src="https://hm.baidu.com/hm.js?abc"></script>'
    d = _directives(build_csp(snippet, "https://edge.example.com"))
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    assert d["script-src"] == ["'self'", f"'sha256-{digest}'", "https://hm.baidu.com"]
    assert d["img-src"] == ["'self'", "data:", "blob:", "https://edge.example.com", "https://hm.baidu.com"]
    # iPhone 存相册要 fetch 边缘节点上的图片；推特视频开着代理的直接从推特拿
    assert d["connect-src"] == ["'self'", "https://edge.example.com", "https://video.twimg.com", "https://hm.baidu.com"]
    assert d["media-src"] == ["'self'", "blob:", "https://video.twimg.com"]


def test_edge_media_origin_allowed_for_video_and_fetch():
    d = _directives(build_csp("", "https://edge.example.com", "https://edge.example.com"))
    assert d["media-src"] == ["'self'", "blob:", "https://edge.example.com", "https://video.twimg.com"]
    # 两个开关同一个域名，只列一次
    assert d["connect-src"] == ["'self'", "https://edge.example.com", "https://video.twimg.com"]


def test_legacy_ad_configuration_is_ignored(monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_ADSENSE_CLIENT", "ca-pub-1234567890123456")
    client = TestClient(web.app)
    for path in ("/", "/guides", "/stats", "/no-such-page-for-ads", "/ads.txt"):
        reply = client.get(path)
        assert "adsbygoogle" not in reply.text
        assert "ca-pub-" not in reply.text
        csp = reply.headers.get("content-security-policy", "")
        assert "google" not in csp and "doubleclick" not in csp
        if reply.headers.get("content-type", "").startswith("text/html"):
            assert _directives(csp)["script-src"] == ["'self'"]
    assert client.get("/ads.txt").status_code == 404


@pytest.mark.parametrize(
    ("placement", "key", "width", "height"),
    [
        ("mobile", "2ea4abab0d473f37f063621df7d49edc", 320, 50),
        ("desktop", "b08de1e1c36939e9944cc6ba2c9831be", 728, 90),
    ],
)
def test_banner_document_is_sandboxed_and_not_indexed(placement, key, width, height):
    client = TestClient(web.app)
    response = client.get(f"/ads/banner/{placement}")
    assert response.status_code == 200
    assert f'data-banner-width="{width}" data-banner-height="{height}"' in response.text
    assert f'src="https://bauval.org/22/{key}"' in response.text
    assert "<script>" not in response.text and "app.js" not in response.text
    directives = _directives(response.headers["content-security-policy"])
    assert directives["script-src"] == ["'self'", "https://bauval.org"]
    assert directives["frame-ancestors"] == ["'self'"]
    assert set(directives["sandbox"]) == {"allow-scripts", "allow-popups", "allow-popups-to-escape-sandbox"}
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "origin"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["cache-control"] == "no-store"
    head = client.head(f"/ads/banner/{placement}")
    assert head.status_code == 200 and head.content == b""


def test_only_known_banner_placements_are_served():
    assert TestClient(web.app).get("/ads/banner/unknown").status_code == 404


def test_banner_documents_do_not_count_as_page_views(monkeypatch):
    from qiuyuantu import stats

    seen = []
    monkeypatch.setattr(stats, "enabled", lambda: True)
    monkeypatch.setattr(stats, "ignored_ip", lambda ip: False)
    monkeypatch.setattr(stats, "record", lambda kind, ip, **kw: seen.append(kind))
    client = TestClient(web.app)
    for placement in ("mobile", "desktop"):
        client.get(f"/ads/banner/{placement}", headers={"User-Agent": "Mozilla/5.0"})
    assert seen == []
    client.get("/", headers={"User-Agent": "Mozilla/5.0"})
    assert seen == ["view"]
