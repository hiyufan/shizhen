"""站长测试设备（/test）和服务器自己的请求不计入统计；正常访客照常计。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from parse_video_py import stats, web
from parse_video_py.convert import jobs
from parse_video_py.parser.base import VideoInfo
from parse_video_py.web import limits
from parse_video_py.web import parse as parse_api


@pytest.fixture
def rows(monkeypatch):
    monkeypatch.setattr(stats, "TOKEN", "tok")
    monkeypatch.setattr(stats, "_buf", [])
    for rl in (limits.parse_limit, limits.job_limit):
        monkeypatch.setattr(rl, "_buckets", {})
    parse_api.cache.clear()

    async def fake_parse(url):
        return VideoInfo(video_url="https://v3-web.douyinvod.com/1.mp4", title="t", source="douyin")

    async def always_safe(url):
        return True

    monkeypatch.setattr(parse_api, "parse_video_share_url", fake_parse)
    monkeypatch.setattr(parse_api, "is_safe_url_async", always_safe)
    return stats._buf


def kinds(rows):
    return [r[1] for r in rows]


def test_server_and_private_addresses_are_ignored(monkeypatch):
    monkeypatch.setattr(stats, "IGNORE_IPS", {"129.146.105.41"})
    for ip in ("127.0.0.1", "::1", "10.0.0.237", "172.23.0.1", "129.146.105.41"):
        assert stats.ignored_ip(ip), ip
    for ip in ("8.8.8.8", "240e:3b7::1", "unknown", ""):
        assert not stats.ignored_ip(ip), ip


def test_normal_visitor_is_counted(rows):
    c = TestClient(web.app)
    c.get("/")
    c.get("/api/parse", params={"url": "https://v.douyin.com/a/"})
    assert kinds(rows) == ["view", "parse"]


def test_wrong_token_does_not_turn_test_mode_on(rows):
    c = TestClient(web.app)
    r = c.post("/test", data={"token": "nope"})
    assert r.status_code == 403 and stats.TEST_COOKIE not in r.cookies
    c.get("/")
    assert kinds(rows) == ["view"]


def test_test_device_is_not_counted(rows):
    c = TestClient(web.app)
    r = c.post("/test", data={"token": "tok"})
    assert r.status_code == 200 and "已开启" in r.text
    assert c.cookies.get(stats.TEST_COOKIE) == stats.test_cookie_value()
    c.get("/")
    c.get("/api/parse", params={"url": "https://v.douyin.com/b/"})
    c.get("/test")
    assert rows == []
    # 关掉之后照常计
    c.get("/test", params={"off": 1})
    assert stats.TEST_COOKIE not in c.cookies
    c.get("/")
    assert kinds(rows) == ["view"]


def test_forged_cookie_is_counted(rows):
    c = TestClient(web.app, cookies={stats.TEST_COOKIE: "x" * 32})
    c.get("/")
    assert kinds(rows) == ["view"]


@pytest.mark.parametrize("muted", [True, False])
def test_job_started_by_a_muted_request_is_not_counted(rows, muted):
    # 转换任务是请求里 create_task 出去的，做完才记 job：要靠继承请求时的标记
    async def fn(job):
        await asyncio.sleep(0)

    async def handle_request():
        if muted:
            stats.mute()
        return jobs.start("gif", fn, owner="8.8.8.8")

    async def main():
        job = await asyncio.create_task(handle_request())
        await job.task

    asyncio.run(main())
    assert kinds(rows) == ([] if muted else ["job"])


def test_links_from_github_issues_are_not_counted(rows):
    # 反馈 issue 里「在线复现」的链接带 src=issue：公开 issue 会被链接扫描 / 爬虫打开，不能算用户
    c = TestClient(web.app)
    c.get("/", params={"url": "https://v.douyin.com/c/", "src": "issue"})
    c.get("/api/parse", params={"url": "https://v.douyin.com/c/", "src": "issue"})
    assert rows == []


_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize(
    ("ua", "referer", "counted"),
    [
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148", "", []),
        ("curl/8.14.1", "", []),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "", []),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_1 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) CriOS/137.0.7151.107 Mobile/15E148 Safari/604.1",
            "https://ynvan.com/",
            ["view", "parse"],
        ),
        # 快捷指令是真人，本来就不带 Referer
        ("BackgroundShortcutRunner/5037.111 CFNetwork/3896.100.1.2.1 Darwin/27.0.0", "", ["view", "parse"]),
        (_CHROME, "https://ynvan.com/", ["view", "parse"]),
        (_CHROME, "", ["view"]),  # 浏览器 UA 不经网页直接调接口（2026-10-17）
    ],
)
def test_scripts_are_not_counted(rows, ua, referer, counted):
    """脚本换着 IP 批量解析会把用户数撑大好几倍（2026-10-13）：按 UA / Referer 认出来，不计。"""
    c = TestClient(web.app, headers={"User-Agent": ua})
    c.get("/")
    c.get("/api/parse", params={"url": "https://v.douyin.com/a/"}, headers={"Referer": referer} if referer else {})
    assert kinds(rows) == counted
