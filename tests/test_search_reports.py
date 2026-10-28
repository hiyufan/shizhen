"""站长报表：离线平台替身、独立缓存和凭证边界。"""

import asyncio
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from qiuyuantu import search_report_store as store
from qiuyuantu import search_reports, stats, web, windsor


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("PARSE_VIDEO_WINDSOR_API_KEY", "private-test-key")
    monkeypatch.setenv("PARSE_VIDEO_SEARCH_GOOGLE_SITE", "sc-domain:example.com")
    monkeypatch.setenv("PARSE_VIDEO_SEARCH_BING_SITE", "https://example.com")
    monkeypatch.setattr(stats, "TOKEN", "stats-test-token")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "search.db")
    monkeypatch.setattr(search_reports, "_date_range", lambda: ("2026-05-01", "2026-10-27"))
    monkeypatch.setattr(search_reports, "_syncing", set())


def row(day="2026-10-25", *, clicks=2, impressions=10, position=3.0):
    return {"date": day, "label": "", "clicks": clicks, "impressions": impressions, "position": position}


def saved_report(rows=None):
    return {
        "daily": {"rows": rows if rows is not None else [row()], "limited": False},
        "queries": {"rows": [{**row(), "label": "test search"}], "limited": False},
        "pages": {"rows": [{**row(), "label": "https://example.com/gif"}], "limited": False},
    }


def save_google(rows=None):
    store.save("google", "sc-domain:example.com", "2026-05-01", "2026-10-27", saved_report(rows))


async def fake_fetch(connector, key, params):
    assert key == "private-test-key"
    site = "example.com" if connector == "searchconsole" else "https://example.com"
    day = "2026-10-26" if connector == "searchconsole" else "2026-10-24"
    fields = params["fields"].split(",")
    assert params["select_accounts"] in ("sc-domain:example.com", "https://example.com")
    record = {"date": day, "site": site, "clicks": 1, "impressions": 5}
    if "position" in fields:
        assert params["include_fresh_data"] == "false"
        assert json.loads(params["filter"]) == [["search_type", "eq", "web"]]
        record["position"] = 2
    if "query" in fields:
        record["query"] = "test keyword"
    if "page" in fields:
        record["page"] = "https://example.com/gif?private=discard#fragment"
    return [record], False


async def test_sync_stores_distinct_sources_and_latest_dates(configured, monkeypatch):
    monkeypatch.setattr(windsor, "fetch", fake_fetch)
    await search_reports.sync_once()
    sources = search_reports.summary()["providers"]
    assert [p["state"] for p in sources] == ["ready", "ready"]
    assert [p["report"]["latest"] for p in sources] == ["2026-10-26", "2026-10-24"]
    assert sources[1]["report"]["totals"]["position"] is None
    assert sources[1]["report"]["pages"] == []
    assert sources[0]["report"]["pages"][0]["label"] == "https://example.com/gif"
    serialized = json.dumps(sources)
    assert "private-test-key" not in serialized and "discard" not in serialized
    assert b"private-test-key" not in store.DB_PATH.read_bytes()


async def test_failed_detail_keeps_entire_previous_snapshot(configured, monkeypatch):
    save_google()
    old = store.snapshot("google", "sc-domain:example.com")
    monkeypatch.setattr(search_reports, "SYNC_INTERVAL", 0)

    async def fail_details(connector, key, params):
        if "query" in params["fields"]:
            raise windsor.ReportError("authorization")
        return await fake_fetch(connector, key, params)

    monkeypatch.setattr(windsor, "fetch", fail_details)
    await search_reports.sync_provider("google", "sc-domain:example.com")
    result = search_reports.summary()["providers"][0]
    assert result["state"] == "stale" and result["error"] == "authorization"
    assert store.snapshot("google", "sc-domain:example.com") == old


async def test_repeated_or_concurrent_sync_is_coalesced(configured, monkeypatch):
    calls = []

    async def slow(connector, key, params):
        calls.append(params)
        await asyncio.sleep(0.01)
        return await fake_fetch(connector, key, params)

    monkeypatch.setattr(windsor, "fetch", slow)
    await asyncio.gather(*(search_reports.sync_provider("google", "sc-domain:example.com") for _ in range(3)))
    await search_reports.sync_provider("google", "sc-domain:example.com")
    assert len(calls) == 3  # 日期、关键词和页面各请求一次


async def test_cancel_does_not_replace_snapshot_or_poison_sync_guard(configured, monkeypatch):
    save_google()
    monkeypatch.setattr(search_reports, "SYNC_INTERVAL", 0)
    started = asyncio.Event()

    async def pending(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(windsor, "fetch", pending)
    task = asyncio.create_task(search_reports.sync_provider("google", "sc-domain:example.com"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not search_reports._syncing
    assert store.snapshot("google", "sc-domain:example.com")["reports"] == saved_report()


def test_weighted_position_ctr_and_previous_equal_period(configured):
    save_google(
        [
            row("2026-10-26", clicks=1, impressions=100, position=2),
            row("2026-10-25", clicks=1, impressions=10, position=20),
            row("2026-10-19", clicks=4),
        ]
    )
    report = search_reports.summary(7)["providers"][0]["report"]
    assert (report["date_from"], report["date_to"]) == ("2026-10-20", "2026-10-26")
    assert report["totals"]["ctr"] == pytest.approx(2 / 110)
    assert report["totals"]["position"] == pytest.approx(400 / 110)
    assert report["previous"]["clicks"] == 4
    assert len(report["daily"]) == 2  # 不给缺失日期填零


def test_unconfigured_pending_empty_and_stale_are_distinct(configured, monkeypatch):
    assert search_reports.summary()["providers"][0]["state"] == "pending"
    save_google([])
    empty = search_reports.summary()["providers"][0]
    assert empty["state"] == "ready" and empty["report"]["totals"] is None
    monkeypatch.setattr(search_reports, "SYNC_INTERVAL", -1)
    assert search_reports.summary()["providers"][0]["state"] == "stale"
    monkeypatch.delenv("PARSE_VIDEO_WINDSOR_API_KEY")
    assert search_reports.summary()["providers"][0] == {
        "provider": "google",
        "account": "sc-domain:example.com",
        "state": "unconfigured",
        "report": None,
    }


def test_new_site_cannot_read_previous_site_snapshot(configured, monkeypatch):
    save_google()
    monkeypatch.setenv("PARSE_VIDEO_SEARCH_GOOGLE_SITE", "sc-domain:new.example.com")
    assert search_reports.summary()["providers"][0]["report"] is None
    store.save("google", "sc-domain:new.example.com", "2026-05-01", "2026-10-27", saved_report())
    assert store.snapshot("google", "sc-domain:example.com") is None


@pytest.mark.parametrize(
    "change",
    [
        {"clicks": -1},
        {"impressions": "10"},
        {"position": float("nan")},
        {"date": "2026-13-01"},
        {"site": "other.example"},
    ],
)
def test_invalid_remote_rows_are_not_saved(change):
    record = {"date": "2026-10-25", "site": "example.com", "clicks": 1, "impressions": 3, "position": 1, **change}
    with pytest.raises(windsor.ReportError):
        store.normalize([record], "daily", "sc-domain:example.com", "2026-10-01", "2026-10-27")


def test_auth_private_cache_range_validation_and_no_network_on_page_refresh(configured, monkeypatch):
    client = TestClient(web.app)
    assert client.get("/api/stats/search").status_code == 404
    headers = {"Authorization": "Bearer stats-test-token"}
    response = client.get("/api/stats/search?days=28", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["providers"][0]["state"] == "pending"
    assert not store.DB_PATH.exists()
    assert client.get("/api/stats/search?days=365", headers=headers).status_code == 422
    page = client.get("/stats", headers=headers)
    assert "private-test-key" not in page.text
    assert 'id="search-performance"' in page.text
    monkeypatch.setattr(stats, "TOKEN", "")
    assert not search_reports.enabled()
    assert client.get("/api/stats/search", headers=headers).status_code == 404


@pytest.mark.parametrize(
    "status,code",
    [(401, "authorization"), (403, "authorization"), (429, "rate_limit"), (302, "upstream"), (202, "pending")],
)
async def test_upstream_failures_do_not_leak_key_or_follow_redirects(monkeypatch, caplog, status, code):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://other.example/"}, json={"error": str(request.url)})

    monkeypatch.setattr(
        windsor, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(windsor.ReportError, match=code) as error:
        await windsor.fetch("searchconsole", "private-test-key", {"fields": "date"})
    assert len(requests) == 1
    assert "private-test-key" not in str(error.value) + caplog.text
    assert not windsor._private_request.get()


async def test_successful_requests_hide_http_logging_without_muting_other_work(monkeypatch, caplog):
    async def handler(request):
        logging.getLogger("httpcore.http11").debug("request %s", request.url)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(
        windsor, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
    )
    with caplog.at_level(logging.DEBUG):
        assert await windsor.fetch("bing_webmaster", "private-test-key", {}) == ([], False)
        logging.getLogger("httpx").info("unrelated request remains visible")
    assert "private-test-key" not in caplog.text
    assert "unrelated request remains visible" in caplog.text


async def test_timeout_retry_preserves_only_fixed_error_code(configured, monkeypatch):
    async def fail(*args):
        raise RuntimeError("remote response contains private-test-key")

    monkeypatch.setattr(windsor, "fetch", fail)
    await search_reports.sync_provider("google", "sc-domain:example.com")
    result = search_reports.summary()["providers"][0]
    assert result["state"] == "error" and result["error"] == "unavailable"
    assert "private-test-key" not in json.dumps(result)
    assert time.time() - result["attempted"] < 10
