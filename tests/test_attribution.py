"""来源归因必须隔离会话、不泄露分享参数，并与原有统计兼容。"""

import asyncio
import dataclasses
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from qiuyuantu import attribution, stats, web
from qiuyuantu.convert import jobs
from qiuyuantu.parser.base import VideoInfo
from qiuyuantu.web import limits
from qiuyuantu.web import parse as parse_api


@pytest.fixture
def measured(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "TOKEN", "test-token")
    monkeypatch.setattr(stats, "DB_PATH", tmp_path / "stats.db")
    monkeypatch.setattr(stats, "_buf", [])
    monkeypatch.setattr(stats, "_traffic_buf", [])
    monkeypatch.setattr(stats, "_recent_downloads", {})
    monkeypatch.setattr(limits.parse_limit, "_buckets", {})
    parse_api.cache.clear()

    async def parse(_url):
        return VideoInfo(video_url="https://v3-web.douyinvod.com/1.mp4", cover_url="", title="test", source="douyin")

    async def safe(_url):
        return True

    monkeypatch.setattr(parse_api, "parse_video_share_url", parse)
    monkeypatch.setattr(parse_api, "is_safe_url_async", safe)
    return TestClient(web.app, base_url="https://ynvan.test")


def report():
    stats.flush()
    return stats.summary(time.time() - 86400, time.time() + 1, 3600)["traffic"]


@pytest.mark.parametrize(
    ("referrer", "expected"),
    [
        ("https://www.google.com/search?q=private", ("search", "google", "organic", "")),
        ("https://m.baidu.com/s?word=private", ("search", "baidu", "organic", "")),
        ("https://cn.bing.com/search?q=private", ("search", "bing", "organic", "")),
        ("https://www.google.com.evil.test/", ("referral", "www.google.com.evil.test", "referral", "")),
        ("https://t.me/private-channel/123", ("social", "telegram", "referral", "")),
        ("https://github.com/owner/repo", ("referral", "github.com", "referral", "")),
        ("https://ynvan.test/gif?url=private", None),
        ("https://[broken", None),
        ("https://user:password@private.test/path", None),
    ],
)
def test_referrer_keeps_only_classified_host(referrer, expected):
    assert attribution.origin(referrer, "ynvan.test", {}) == expected


def test_signed_cookie_expiry_and_tampering(monkeypatch):
    monkeypatch.setattr(attribution.time, "time", lambda: 1000)
    visit = attribution.choose(None, "/gif", None)
    cookie = attribution.encode(visit)
    assert attribution.decode(cookie) == visit
    assert attribution.decode(cookie + "x") is None
    assert attribution.decode("a.中文") is None
    monkeypatch.setattr(attribution.time, "time", lambda: 2801)
    assert attribution.decode(cookie) is None


def test_source_survives_navigation_and_api_and_new_campaign_starts_session(measured):
    response = measured.get("/gif", headers={"referer": "https://www.google.com/search?q=private"})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert response.headers["cache-control"] == "private, no-cache"
    measured.get("/guide/video-to-gif", headers={"referer": "https://ynvan.test/gif"})
    response = measured.get("/api/parse", params={"url": "https://v.douyin.com/test/"})
    assert attribution.COOKIE not in response.cookies
    result = report()
    assert result["totals"]["visits"] == 1
    assert result["totals"]["views"] == 2
    assert result["totals"]["parsed"] == 1
    assert result["search_pages"][0]["landing"] == "/gif"
    params = {"utm_source": "telegram", "utm_medium": "social", "utm_campaign": "launch"}
    measured.get("/", params=params)
    measured.get("/", params=params)
    assert report()["totals"]["visits"] == 2
    assert "private" not in json.dumps(report())


def test_invalid_tags_and_sensitive_urls_are_not_stored(measured):
    measured.get(
        "/",
        params={
            "utm_source": "name@example.com",
            "utm_campaign": "secret",
            "url": "https://signal.art/#pack_key=secret",
        },
    )
    result = report()
    assert result["channels"][0]["channel"] == "direct"
    assert result["landing_pages"][0]["landing"] == "/"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("path", ["/missing", "/stats", "/test", "/favicon.ico", "/api/health"])
def test_non_entry_pages_do_not_create_sessions(measured, path):
    response = measured.get(path)
    assert attribution.COOKIE not in response.cookies
    assert report()["totals"]["visits"] == 0


def test_bots_head_and_test_devices_are_excluded(measured):
    measured.head("/")
    measured.get("/", headers={"user-agent": "Googlebot"})
    measured.post("/test", data={"token": "test-token"})
    response = measured.get("/")
    assert attribution.COOKIE not in response.cookies
    assert report()["started"] is None


def test_independent_clients_and_missing_cookies_do_not_share_sources(measured):
    measured.get("/", headers={"referer": "https://www.google.com/"})
    other = TestClient(web.app, base_url="https://ynvan.test")
    other.get("/api/parse", params={"url": "https://v.douyin.com/test/"})
    result = report()
    assert result["totals"]["parsed"] == 0
    assert result["unassigned_events"] == 1
    other.get("/")
    assert {row["channel"] for row in report()["channels"]} == {"direct", "search"}


def test_background_job_keeps_origin_after_request_context_ends(measured):
    measured.get("/", params={"utm_source": "github", "utm_campaign": "readme"})
    visit = attribution.decode(measured.cookies.get(attribution.COOKIE))

    async def finish():
        await asyncio.sleep(0)
        stats.record("job", "8.8.8.8", source="gif")

    async def run():
        with stats.request_context(muted=False, visit=visit):
            task = asyncio.create_task(finish())
        with stats.request_context(muted=False, visit=None):
            await task

    asyncio.run(run())
    assert report()["channels"][0]["converted"] == 1


def test_downloads_count_actions_not_previews_or_retries(measured, tmp_path, monkeypatch):
    path = tmp_path / "result.gif"
    path.write_bytes(b"GIF89a")
    job = jobs.Job(id="test", type="gif", status="done", result_path=str(path))
    monkeypatch.setattr(jobs, "get", lambda _id: job)
    measured.get("/gif", params={"utm_source": "github"})
    assert measured.get("/api/jobs/test/file?inline=1").status_code == 200
    assert report()["totals"]["downloaded"] == 0
    measured.get("/api/jobs/test/file")
    measured.get("/api/jobs/test/file")
    result = report()
    assert result["totals"]["downloaded"] == 1
    with stats._db() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE kind='download'").fetchone()[0] == 1


def test_legacy_database_is_preserved_and_not_mislabeled(measured):
    with sqlite3.connect(stats.DB_PATH) as conn:
        conn.executescript(stats._SCHEMA)
        conn.execute("INSERT INTO events VALUES (?, 'view', 'old', '/', 1, '', 0)", (time.time(),))
    measured.get("/")
    result = report()
    assert result["historical_events"] == 1
    assert result["totals"]["visits"] == 1
    assert result["channels"][0]["channel"] == "direct"


def test_funnel_uses_sessions_started_in_range_and_includes_cached_success(measured):
    now = time.time()
    visit = dataclasses.replace(attribution.choose(None, "/gif", None), started=now - 7200)
    with stats.request_context(muted=False, visit=visit):
        stats.record("view", "8.8.8.8", source="/gif")
        stats.record("parse", "8.8.8.8", reason="cache")
        stats.record("job", "8.8.8.8", source="prepare")
    stats.flush()
    assert stats.summary(now - 3600, now + 1, 3600)["traffic"]["totals"]["visits"] == 0
    result = stats.summary(now - 86400, now + 1, 3600)["traffic"]["totals"]
    assert result["parsed"] == 1 and result["converted"] == 0


def test_admin_report_requires_auth_and_is_not_cached(measured):
    assert measured.get("/api/stats").status_code == 404
    response = measured.get("/api/stats", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "traffic" in response.json()


def test_excluded_download_does_not_consume_real_visitors_deduplication(measured):
    with stats.request_context(muted=True, visit=None):
        stats.record_download("8.8.8.8", "file")
    stats.record_download("8.8.8.8", "file")
    assert [row[1] for row in stats._buf] == ["download"]


def test_prune_cleans_attribution_without_losing_collection_start(measured):
    measured.get("/")
    first = report()["started"]
    old = time.time() - (stats.RETENTION_DAYS + 1) * 86400
    with stats._db() as conn:
        conn.execute("UPDATE events SET ts = ?", (old,))
        conn.execute("UPDATE traffic_visits SET started = ?", (old,))
        conn.execute("UPDATE traffic_events SET ts = ?", (old,))
    assert stats.prune() == 1
    with stats._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM traffic_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM traffic_visits").fetchone()[0] == 0
    assert report()["started"] == first
