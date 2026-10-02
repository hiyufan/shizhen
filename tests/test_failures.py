"""解析失败的链接留 7 天：真实用户的失败才记，站长测试 / 服务器自己的不记；/api/stats 里能看到。"""

import time

import pytest
from fastapi.testclient import TestClient

from parse_video_py import failures, stats, web
from parse_video_py.parser.errors import ParseError
from parse_video_py.web import limits
from parse_video_py.web import parse as parse_api

LINK = "https://www.kuaishou.com/f/X-abc"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(failures, "DB_PATH", tmp_path / "failures.db")
    monkeypatch.setattr(stats, "TOKEN", "tok")
    monkeypatch.setattr(stats, "DB_PATH", tmp_path / "stats.db")
    monkeypatch.setattr(limits.parse_limit, "_buckets", {})
    parse_api.cache.clear()

    async def fail(url):
        raise ParseError("unsupported", "快手链接里没有作品 ID")

    async def always_safe(url):
        return True

    monkeypatch.setattr(parse_api, "parse_video_share_url", fail)
    monkeypatch.setattr(parse_api, "is_safe_url_async", always_safe)
    return TestClient(web.app)


def test_real_visitor_failure_is_kept_and_shown_on_stats(client, monkeypatch):
    # TestClient 的来源是 testclient，不是内网地址，算真实访客
    client.get("/api/parse", params={"url": f"看看 {LINK}"})
    parse_api.cache.clear()
    client.get("/api/parse", params={"url": LINK})
    rows = client.get("/api/stats", params={"token": "tok"}).json()["failures"]
    assert [(r["link"], r["platform"], r["reason"], r["n"]) for r in rows] == [(LINK, "kuaishou", "unsupported", 2)]


def test_test_device_failures_are_not_kept(client):
    client.post("/test", data={"token": "tok"})
    client.get("/api/parse", params={"url": LINK})
    assert failures.recent(0) == []


def test_old_failures_are_pruned(client, monkeypatch):
    failures.record(LINK, "kuaishou", "unsupported", "x")
    monkeypatch.setattr(failures.time, "time", lambda: time.time_ns() / 1e9 + 8 * 86400)
    assert failures.prune() == 1 and failures.recent(0) == []
