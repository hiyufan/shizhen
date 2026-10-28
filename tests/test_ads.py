"""广告仅在隔离文档执行，不能放宽工具页脚本策略或增加虚假浏览量。"""

import re

import pytest
from fastapi.testclient import TestClient

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
    assert "script-src 'self';" in page.headers["content-security-policy"]
    assert 'data-banner="/api/ads/"' in page.text
    assert "data-banner" not in client.get("/guide/video-to-gif").text


def test_ads_can_be_disabled_and_unknown_placements_never_load_scripts(monkeypatch):
    client = TestClient(web.app)
    assert client.get("/api/ads/unknown").status_code == 404
    monkeypatch.setattr(ads, "ENABLED", False)
    assert client.get("/api/ads/mobile").status_code == 404
    assert 'data-banner="/api/ads/"' not in client.get("/").text
