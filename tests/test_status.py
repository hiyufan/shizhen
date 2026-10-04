"""公开的 /status 页：自检怎么判平台好坏、页面数据怎么算。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from parse_video_py import status, web
from parse_video_py.parser.errors import ParseError


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "DB_PATH", tmp_path / "status.db")
    monkeypatch.setattr(status.stats, "DB_PATH", tmp_path / "stats.db")


def _parser(results: dict):
    async def parse(link):
        outcome = results[link]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return parse


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ({"a": "info"}, (True, "")),
        ({"a": ParseError("blocked", "限流了")}, (False, "blocked")),
        # 第一条作品被删了：换下一条，平台本身是好的
        ({"a": ParseError("deleted", "没了"), "b": "info"}, (True, "")),
        # 全删了：记成链接失效，不算平台失败
        ({"a": ParseError("deleted", "没了"), "b": ParseError("deleted", "也没了")}, (False, "canary")),
    ],
)
def test_probe_one(results, expected):
    ok, reason, _ = asyncio.run(status.probe_one("douyin", list(results), _parser(results)))
    assert (ok, reason) == expected


def test_probe_links_from_env(monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_STATUS_PROBES", '{"redbook": ["https://xhslink.cn/o/x"], "nope": ["y"]}')
    links = status.probe_links()
    assert links["redbook"] == ["https://xhslink.cn/o/x"] and "nope" not in links
    assert links["douyin"]  # 代码里自带的还在


def test_snapshot_and_headline():
    now = 1_790_000_000.0
    for hours_ago in (30, 2, 1):
        status.record("douyin", True, ms=900, ts=now - hours_ago * 3600)
    status.record("youtube", False, "login", ts=now - 600)
    status.record("bilibili", False, "canary", ts=now - 600)  # 链接失效不算数
    snap = status.snapshot(now)
    by_key = {x["platform"].key: x for x in snap["platforms"]}
    assert by_key["douyin"]["last"]["ok"] and by_key["douyin"]["week_ok"] == 3 and by_key["douyin"]["median_ms"] == 900
    assert by_key["bilibili"]["last"] is None
    assert snap["down"] == ["YouTube"] and snap["n_checked"] == 2
    assert "YouTube 解析失败，其余 1 个正常" in status.headline(snap)
    assert sum(d["n"] for d in by_key["douyin"]["days"]) == 3


def test_headline_before_first_probe():
    assert "收集中" in status.headline(status.snapshot())


def test_user_stats_skip_user_side_failures(monkeypatch):
    monkeypatch.setattr(status.stats, "TOKEN", "t")
    monkeypatch.setattr(status.stats, "ignored_ip", lambda ip: False)
    for ok, reason in ((True, ""), (False, "blocked"), (False, "deleted"), (True, "cache")):
        status.stats.record("parse", "1.2.3.4", source="kuaishou", ok=ok, reason=reason)
    status.stats.flush()
    kuaishou = {x["platform"].key: x for x in status.snapshot()["platforms"]}["kuaishou"]
    assert kuaishou["users"] == {"n": 2, "ok": 1}


def test_status_page_renders():
    status.record("douyin", True, ms=1200)
    r = TestClient(web.app).get("/status")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
    assert "各平台解析状态" in r.text and "1 个平台全部能正常解析" in r.text and "FAQPage" in r.text
    assert "/status" in TestClient(web.app).get("/sitemap.xml").text
