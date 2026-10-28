"""受众分类不能把脚本、相同出口的不同会话和没有证据的历史记录混成真人。"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from qiuyuantu import attribution, audience, stats, web
from qiuyuantu.parser.base import VideoInfo
from qiuyuantu.web import audience as activity_api
from qiuyuantu.web import limits
from qiuyuantu.web import parse as parse_api

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ACTIVITY = {"visible": True, "interaction": True, "automated": False}


@pytest.fixture
def measured(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "TOKEN", "test-token")
    monkeypatch.setattr(stats, "DB_PATH", tmp_path / "stats.db")
    for name, value in (("_buf", []), ("_traffic_buf", []), ("_evidence_buf", {}), ("_signal_buf", [])):
        monkeypatch.setattr(stats, name, value)
    monkeypatch.setattr(activity_api.activity_limit, "_buckets", {})
    monkeypatch.setattr(limits.parse_limit, "_buckets", {})
    parse_api.cache.clear()

    async def fake_parse(_url):
        return VideoInfo(source="douyin", title="test", video_url="https://example.com/test.mp4", cover_url="")

    async def safe(_url):
        return True

    monkeypatch.setattr(parse_api, "parse_video_share_url", fake_parse)
    monkeypatch.setattr(parse_api, "is_safe_url_async", safe)
    return TestClient(web.app, base_url="https://ynvan.test", headers={"user-agent": CHROME})


def report(selected="human"):
    stats.flush()
    return stats.summary(time.time() - 86400, time.time() + 1, 3600, selected=selected)


def interact(client, **fields):
    return client.post("/api/visit", json={**ACTIVITY, **fields}, headers={"origin": "https://ynvan.test"})


def parse(client):
    return client.get(
        "/api/parse", params={"url": "https://v.douyin.com/test/"}, headers={"referer": "https://ynvan.test/"}
    )


def test_default_is_strict_but_json_api_remains_available(measured):
    measured.get("/")
    assert parse(measured).json()["code"] == 200
    assert report()["totals"]["users"] == 0
    assert report("unknown")["totals"]["users"] == 1
    response = measured.get("/api/stats", headers={"Authorization": "Bearer test-token"})
    assert response.json()["audience"]["selected"] == "human"
    assert measured.get("/api/stats", params={"token": "test-token", "audience": "invalid"}).status_code == 422


def test_activity_promotes_prior_events_and_filters_source_cohort(measured):
    measured.get("/", params={"utm_source": "github"})
    parse(measured)
    assert report()["traffic"]["totals"]["visits"] == 0
    response = interact(measured)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    result = report()
    assert result["totals"]["users"] == 1
    assert result["traffic"]["totals"]["views"] == 1
    assert result["traffic"]["channels"][0]["source"] == "github"
    assert result["traffic"]["totals"]["parsed"] == 1


def test_shared_ip_and_new_campaign_do_not_inherit_other_sessions(measured):
    measured.get("/")
    interact(measured)
    other = TestClient(web.app, base_url="https://ynvan.test", headers={"user-agent": CHROME})
    other.get("/", params={"utm_source": "other"})
    parse(other)
    assert report("unknown")["totals"]["users"] == 1
    assert report()["totals"]["users"] == 0
    measured.get("/", params={"utm_source": "new"})
    parse(measured)
    assert report()["traffic"]["totals"]["visits"] == 1
    assert report()["totals"]["users"] == 0


@pytest.mark.parametrize("fields", [{"visible": False}, {"interaction": False}])
def test_inactive_page_does_not_gain_human_status(measured, fields):
    measured.get("/")
    assert audience.COOKIE not in interact(measured, **fields).cookies
    assert report()["totals"]["view"] == 0


def test_origin_cookie_and_payload_validation(measured):
    measured.get("/")
    assert measured.post("/api/visit", json=ACTIVITY).status_code == 403
    assert measured.post("/api/visit", json=ACTIVITY, headers={"origin": "https://evil.test"}).status_code == 403
    assert (
        measured.post(
            "/api/visit", json=ACTIVITY, headers={"origin": "https://ynvan.test", "sec-fetch-site": "cross-site"}
        ).status_code
        == 403
    )
    assert (
        measured.post(
            "/api/visit", json={**ACTIVITY, "link": "private"}, headers={"origin": "https://ynvan.test"}
        ).status_code
        == 422
    )
    measured.cookies.clear()
    assert audience.COOKIE not in interact(measured).cookies


def test_cookie_expiry_tampering_and_session_binding(monkeypatch):
    monkeypatch.setattr(audience.time, "time", lambda: 1000)
    cookie = audience.activity_cookie("session", "human")
    assert audience.activity_evidence(cookie, "session").status == "human"
    assert audience.activity_evidence(cookie + "中文", "session").status == "unknown"
    assert audience.activity_evidence(cookie, "other").status == "unknown"
    monkeypatch.setattr(audience.time, "time", lambda: 2801)
    assert audience.activity_evidence(cookie, "session").status == "unknown"


def test_machine_session_cannot_replay_itself_into_human(measured):
    measured.get("/")
    interact(measured, automated=True)
    parse(measured)
    report()
    interact(measured)
    assert report()["totals"]["users"] == 0
    assert report("machine")["totals"]["users"] == 1


@pytest.mark.parametrize("agent", ["GoogleOther", "Google-InspectionTool/1.0", "node", "n8n", "HeadlessChrome/131"])
def test_new_automation_agents_are_classified(agent):
    assert audience.agent_evidence(agent).status == "machine"


@pytest.mark.parametrize(
    "suffix",
    ["NewsArticle/18.9.0 GoldBrowser/18.9.0", "Quark/10.19.5.1170 Build/AP3A.240617.008", "HeyTapBrowser/40.0"],
)
def test_mobile_browsers_are_not_blocked_by_broad_library_patterns(suffix):
    assert audience.agent_evidence(CHROME + " " + suffix).status == "unknown"
    assert audience.browser_agent(CHROME + " " + suffix)


def test_background_task_captures_visit_and_late_activity(measured):
    measured.get("/")
    visit = attribution.decode(measured.cookies.get(attribution.COOKIE))

    async def run():
        with stats.request_context(muted=False, visit=visit):
            task = asyncio.create_task(finish())
        await task

    async def finish():
        await asyncio.sleep(0)
        stats.record("job", "8.8.8.8", source="gif")

    asyncio.run(run())
    interact(measured)
    assert report()["totals"]["job_ok"] == 1


def test_rate_review_counts_only_independent_parses(measured):
    visit = attribution.choose(None, "/", None)
    with stats.request_context(muted=False, visit=visit, evidence=audience.Evidence("human", "browser-interaction")):
        for _ in range(30):
            stats.record("job", "8.8.8.8", source="prepare")
            stats.record("parse", "8.8.8.8", reason="cache")
    assert report()["totals"]["users"] == 1
    with stats.request_context(muted=False, visit=visit, evidence=audience.Evidence("human", "browser-interaction")):
        for _ in range(20):
            stats.record("parse", "8.8.8.8", source="douyin")
    assert report()["totals"]["users"] == 0
    stats.record_activity(visit, "human")
    assert report()["totals"]["users"] == 0
    assert report("unknown")["totals"]["users"] == 1


def test_prune_cleans_labels_before_rowids_can_be_reused(measured):
    measured.get("/")
    interact(measured)
    report()
    with stats._db() as conn:
        conn.execute("UPDATE events SET ts = ?", (time.time() - 100 * 86400,))
    stats.prune()
    with stats._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_audience").fetchone()[0] == 0
    # 独立无证明请求不能继承旧 rowid 的真人分类。
    with stats.request_context(muted=False, visit=None):
        stats.record("parse", "8.8.4.4", source="douyin")
    assert report()["totals"]["users"] == 0


def test_signed_activity_without_referrer_is_still_usable(measured):
    measured.get("/")
    interact(measured)
    response = measured.get("/api/parse", params={"url": "https://v.douyin.com/test/"})
    assert response.json()["code"] == 200
    assert report()["totals"]["users"] == 1


def test_identical_timestamps_preserve_each_events_own_classification(measured, monkeypatch):
    stamp = time.time()
    monkeypatch.setattr(stats.time, "time", lambda: stamp)
    for status in ("human", "unknown", "machine"):
        with stats.request_context(muted=False, visit=None, evidence=audience.Evidence(status, "test-evidence")):
            stats.record("parse", "8.8.8.8", source="douyin")
    stats.flush()
    for status in ("human", "unknown", "machine"):
        assert stats.summary(stamp - 1, stamp + 1, 60, selected=status)["totals"]["parse"] == 1
