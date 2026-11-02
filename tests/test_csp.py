"""内容安全策略：不许跑内联脚本；站长配的统计代码按哈希 / 域名单独放行，AdSense 只在配置后放行。"""

import base64
import hashlib

from fastapi.testclient import TestClient

from qiuyuantu import seo, web
from qiuyuantu.web.middleware import ADSENSE_ORIGINS, build_csp


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


ADSENSE = "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1234567890123456"


def test_adsense_origins_only_when_configured():
    plain = _directives(build_csp())
    assert "frame-src" not in plain and plain["script-src"] == ["'self'"]
    d = _directives(build_csp(adsense=True))
    # 仍然不许内联脚本；广告加载脚本、广告 iframe 与打点只放行 Google 的广告域名
    assert d["script-src"] == ["'self'", *ADSENSE_ORIGINS] and "'unsafe-inline'" not in d["script-src"]
    assert d["frame-src"] == ["'self'", *ADSENSE_ORIGINS]
    assert set(ADSENSE_ORIGINS) <= set(d["img-src"]) and set(ADSENSE_ORIGINS) <= set(d["connect-src"])
    assert d["frame-ancestors"] == ["'none'"] and d["object-src"] == ["'none'"]


def test_adsense_script_only_on_public_pages(monkeypatch):
    client = TestClient(web.app)
    assert ADSENSE not in client.get("/").text
    monkeypatch.setattr(seo, "ADSENSE_CLIENT", "ca-pub-1234567890123456")
    home = client.get("/").text
    assert ADSENSE in home and home.index(ADSENSE) < home.index("</head>")
    assert ADSENSE in client.get("/guides").text
    # 统计后台、错误页不放广告
    assert ADSENSE not in client.get("/stats").text
    missing = client.get("/no-such-page-for-ads")
    assert missing.status_code == 404 and ADSENSE not in missing.text


def test_ads_txt_follows_configuration(monkeypatch):
    client = TestClient(web.app)
    assert client.get("/ads.txt").status_code == 404
    monkeypatch.setattr(seo, "ADSENSE_CLIENT", "ca-pub-1234567890123456")
    reply = client.get("/ads.txt")
    assert reply.status_code == 200
    assert reply.text == "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n"


def test_adsense_client_rejects_anything_but_a_publisher_id():
    assert seo.adsense_client(" ca-pub-1234567890123456 ") == "ca-pub-1234567890123456"
    for bad in ["", "pub-1234567890123456", 'ca-pub-1"><script>alert(1)</script>', "ca-pub-12ab"]:
        assert seo.adsense_client(bad) == ""
