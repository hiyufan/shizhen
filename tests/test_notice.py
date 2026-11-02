"""全站公告：服务端渲染、按语言显示、按编号记住关闭、到期自动消失。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from lxml import html

from qiuyuantu import web
from qiuyuantu.web import notice


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(notice, "TEXT", {"zh": "脚本已移除 <b>请留意</b>", "en": ""})
    monkeypatch.setattr(notice, "LINK", "/guides")
    monkeypatch.setattr(notice, "LEVEL", "warn")
    monkeypatch.setattr(notice, "UNTIL", None)
    monkeypatch.setattr(notice, "ID", "abc123")


def _notice(client, path="/", **headers):
    nodes = html.fromstring(client.get(path, headers=headers).text).xpath("//aside[@data-notice]")
    return nodes[0] if nodes else None


def test_no_notice_without_configuration(monkeypatch):
    monkeypatch.setattr(notice, "TEXT", {"zh": "", "en": ""})
    assert _notice(TestClient(web.app)) is None


@pytest.mark.parametrize("path", ["/", "/douyin", "/guides", "/status"])
def test_notice_renders_before_navigation_on_public_pages(configured, path):
    node = _notice(TestClient(web.app), path)
    assert node is not None
    assert node.get("data-level") == "warn" and node.get("data-notice") == "abc123"
    assert node.get("aria-label") == "网站公告" and "data-nosnippet" in node.attrib
    assert node.getnext().get("class") == "nav"
    assert "脚本已移除 <b>请留意</b>" in node.text_content()  # 站长文案按纯文本转义
    assert node.xpath(".//a/@href") == ["/guides"]
    assert node.xpath(".//button/@aria-label") == ["关闭公告"]


def test_english_pages_need_their_own_text(configured, monkeypatch):
    client = TestClient(web.app)
    assert _notice(client, **{"Accept-Language": "en"}) is None
    monkeypatch.setattr(notice, "TEXT", {"zh": "中文公告", "en": "English notice"})
    node = _notice(client, **{"Accept-Language": "en"})
    assert node.get("aria-label") == "Site notice"
    assert "English notice" in node.text_content() and "中文公告" not in node.text_content()


def test_dismissal_cookie_only_hides_the_same_notice(configured):
    client = TestClient(web.app)
    client.cookies.set(notice.COOKIE, "abc123")
    assert _notice(client) is None
    client.cookies.set(notice.COOKIE, "older")
    assert _notice(client) is not None


def test_notice_expires(configured, monkeypatch):
    monkeypatch.setattr(notice, "UNTIL", datetime.now(timezone.utc) - timedelta(seconds=1))
    assert _notice(TestClient(web.app)) is None
    monkeypatch.setattr(notice, "UNTIL", datetime.now(timezone.utc) + timedelta(hours=1))
    assert _notice(TestClient(web.app)) is not None


def test_until_defaults_to_beijing_time_and_tolerates_typos():
    assert notice._until("2026-12-01T00:00") == datetime(2026, 11, 30, 16, tzinfo=timezone.utc)
    assert notice._until("2026-12-01T00:00+00:00") == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert notice._until("下个月") is None
    assert notice._until("") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/guides", "/guides"),
        ("https://github.com/x", "https://github.com/x"),
        ("javascript:alert(1)", ""),
        ("//evil.example", ""),
        ("http://plain.example", ""),
    ],
)
def test_link_accepts_only_site_paths_and_https(value, expected):
    assert notice._link(value) == expected
