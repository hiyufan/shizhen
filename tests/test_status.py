"""公开的 /status 页：自检怎么判平台好坏、页面数据怎么算。"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from parse_video_py import status, web
from parse_video_py.convert import net, youtube_native
from parse_video_py.parser.base import VideoInfo
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


@pytest.mark.parametrize("platform,enabled", [("youtube", True), ("youtube", False), ("douyin", True)])
async def test_native_probe_has_time_for_complete_download(monkeypatch, platform, enabled):
    budgets = []

    async def wait_for(coro, timeout):
        budgets.append(timeout)
        return await coro

    async def media(_info):
        pass

    monkeypatch.setattr(youtube_native, "enabled", lambda: enabled)
    monkeypatch.setattr(status.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(status, "_probe_youtube_media", media)
    ok, _, _ = await status.probe_one(platform, ["a"], _parser({"a": "info"}))
    expected = status.PROBE_TIMEOUT
    if platform == "youtube" and enabled:
        expected += status.config.YOUTUBE_NATIVE_TIMEOUT
    assert ok and budgets == [expected]


@pytest.mark.parametrize("code,body,expected", [(206, b"media", True), (403, b"denied", False), (200, b"", False)])
async def test_youtube_probe_reads_media_not_just_metadata(monkeypatch, code, body, expected):
    info = VideoInfo(video_url="https://cdn.example/video.mp4", cover_url="", video_headers={"Cookie": "session=test"})

    async def parse(_url):
        return info

    def respond(request):
        assert request.headers["Range"] == "bytes=0-1023"
        assert request.headers["Cookie"] == "session=test"
        return httpx.Response(code, headers={"content-type": "video/mp4"}, stream=httpx.ByteStream(body))

    monkeypatch.setattr(net, "safe_client", lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    ok, reason, _ = await status.probe_one("youtube", ["video"], parse)
    assert ok is expected
    if code == 403:
        assert reason == "blocked"
    elif not body:
        assert reason == "empty"


@pytest.mark.parametrize("second_error,expected", [("blocked", False), ("deleted", True)])
async def test_youtube_checks_second_sample_after_first_succeeds(monkeypatch, second_error, expected):
    checked = []

    async def media(info):
        checked.append(info)

    monkeypatch.setattr(status, "_probe_youtube_media", media)
    parse = _parser({"a": "working-media", "b": ParseError(second_error)})
    ok, _, _ = await status.probe_one("youtube", ["a", "b"], parse)
    assert ok is expected and checked == ["working-media"]


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


T, F = True, False
NET = (F, "timeout")


@pytest.mark.parametrize(
    ("history", "keys"),
    [
        # 2026-10-15 那种：大部分平台连续两轮都超时 → 服务器级，一封就够（不再逐个平台报）
        ({"douyin": [NET, NET], "redbook": [NET, NET], "bilibili": [NET, NET], "youtube": [(T, "")] * 2}, {"server"}),
        ({"douyin": [NET, (T, "")], "redbook": [NET, NET], "bilibili": [NET, NET]}, {"server"}),  # 两个也算「一半以上」
        ({"douyin": [NET, (T, "")], "redbook": [NET, (T, "")], "bilibili": [(T, "")] * 2}, set()),  # 只失败一轮
        # 单个平台连续三轮解析出错：多半改版了
        ({"douyin": [(F, "parse")] * 3, "redbook": [(T, "")] * 3, "bilibili": [(T, "")] * 3}, {"platform:douyin"}),
        # 已知限制、自检链接失效不算
        ({"douyin-note": [(F, "login")] * 3, "douyin": [(F, "canary")] * 3, "x": [(T, "")] * 3}, set()),
    ],
)
def test_alert_rules(history, keys):
    from parse_video_py import alerts

    assert set(alerts.problems(history)) == keys


def test_alert_sent_once_then_recovery(monkeypatch, tmp_path):
    """同一个问题只发一次；恢复了发一封；状态落在文件里，部署重启后不重发。"""
    from parse_video_py import alerts

    sent = []
    monkeypatch.setattr(alerts, "ALERT_TO", "me@example.com")
    monkeypatch.setattr(alerts, "STATE_PATH", tmp_path / "alerts.json")
    monkeypatch.setattr(alerts.feedback, "mail_enabled", lambda: True)
    monkeypatch.setattr(alerts.feedback, "_send_mail", lambda to, subject, text: sent.append((to, subject)))
    down = {p: [NET, NET] for p in ("douyin", "redbook", "bilibili")}
    up = {p: [(T, ""), NET] for p in ("douyin", "redbook", "bilibili")}

    asyncio.run(alerts.check(down))
    asyncio.run(alerts.check(down))  # 还没好：不重发
    assert [s[1] for s in sent] == ["求原图告警：服务器可能连不上外网"]
    asyncio.run(alerts.check(up))
    assert sent[-1] == ("me@example.com", "求原图：自检已恢复") and len(sent) == 2
    asyncio.run(alerts.check(up))
    assert len(sent) == 2 and (tmp_path / "alerts.json").read_text() == "{}"
