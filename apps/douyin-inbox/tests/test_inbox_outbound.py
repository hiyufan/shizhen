"""离线验证上传预算、发送意图恢复、公平性与窗口；不读取账号目录。"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.engine import Engine
from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.pipeline import NativePipeline
from qiuyuantu_inbox.store import Store


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path, Settings(native_pipeline=True, send_interval=0.001, ack_window=2))
    result.bind("account", 1)
    return result


def ready(store, name="one", sender="sender", parts=None):
    now = time.time()
    message = Message(name, "chat-" + sender, sender, now, "work", {"work_id": "123"})
    assert store.ingest(message, now) == "queued"
    job = store.claim("queued", now)
    store.ready(job, "", parts or [{"text": name}], now)
    return job


def plan(store):
    for row in store.unplanned_outbound(set()):
        store.plan_outbound(row, 1 if json.loads(row["payload"]).get("files") else 0, time.time())


def uploaded(store):
    row = store.claim_upload(time.time(), set())
    assert row
    assert store.uploaded(row, {"handle": row["client_id"], "generation": "page"}, time.time())
    return row


def test_single_message_plans_preserve_live_pairs(store):
    parts = [
        {"files": ["a", "b"], "md5": ["a", "b"]},
        {"files": ["a", "v"], "md5": ["a", "v"], "live_pair": True},
    ]
    job = ready(store, parts=parts)
    assert [json.loads(p["payload"])["files"] for p in store.parts(job)] == [["a"], ["b"], ["a", "v"]]
    plan(store)
    assert len(store.unplanned_outbound(set())) == 0


def test_uploads_are_bounded_before_and_after_network(store):
    ready(store, parts=[{"files": [str(i)], "md5": [str(i)]} for i in range(6)])
    plan(store)
    first, second = store.claim_upload(time.time(), set()), store.claim_upload(time.time(), set())
    assert first and second
    assert store.claim_upload(time.time(), set()) is None
    for row in (first, second):
        store.uploaded(row, {"handle": "x", "generation": "page"}, time.time())
    uploaded(store)
    uploaded(store)
    assert store.claim_upload(time.time(), set()) is None  # 最多 workers + ACK window 份预取。


def test_upload_byte_budget_and_scope(store):
    ready(store, parts=[{"files": ["v"], "md5": ["v"]}])
    row = store.unplanned_outbound(set())[0]
    store.plan_outbound(row, store.settings.upload_bytes, time.time())
    ready(store, "two", "other", [{"files": ["x"], "md5": ["x"]}])
    plan(store)
    assert store.claim_upload(time.time(), {"missing"}) is None
    assert store.claim_upload(time.time(), set())["job"] == row["job"]
    assert store.claim_upload(time.time(), set()) is None


def test_restart_discards_uploaded_handles_and_never_retries_submitted(store):
    first = ready(store)
    ready(store, "two", "other", [{"files": ["x"], "md5": ["x"]}])
    plan(store)
    cached = uploaded(store)
    sending = store.claim("ready", time.time())
    assert sending["id"] == first["id"]
    row = store.outbound_part(sending, 0)
    store.begin_outbound(sending, row, time.time())
    store.recover(time.time())
    assert store.outbound_part(sending, 0)["state"] == "submitted"
    assert store.outbound_part({"id": cached["job"]}, 0)["state"] == "queued"
    assert store.get_meta("paused") == "delivery_requires_review"
    assert next(j for j in store.list_jobs() if j["id"] == sending["id"])["state"] == "uncertain"
    store.resolve(sending["id"], "abandon", time.time())
    assert store.outbound_part(sending, 0)["state"] == "abandoned"


def test_cancelled_upload_cannot_become_sendable(store):
    job = ready(store, parts=[{"files": ["x"], "md5": ["x"]}])
    plan(store)
    row = store.claim_upload(time.time(), set())
    with store.transaction() as db:
        db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job["id"],))
    assert not store.uploaded(row, {"handle": "x", "generation": "page"}, time.time())
    assert store.retire_outbound()[0]["state"] == "uploading"
    assert store.claim_upload(time.time(), set()) is None


def test_terminal_upload_failure_produces_notice_preserving_sent_parts(store):
    job = ready(store, parts=[{"files": ["x"], "md5": ["x"]}])
    plan(store)
    for i in range(3):
        row = store.claim_upload(time.time() + i * 100, set())
        store.upload_failed(row, time.time())
    parts = store.parts(job)
    assert len(parts) == 1
    assert "上传暂时失败" in json.loads(parts[0]["payload"])["text"]
    plan(store)
    assert store.claim("ready", time.time())


def test_actual_submit_clock_and_cooldown_are_monotonic(store):
    ready(store)
    plan(store)
    store.sync_outbound_clock(time.time() + 10, time.time() + 30)
    store.sync_outbound_clock(0, 0)
    assert store.claim("ready", time.time() + 20) is None
    assert store.claim("ready", time.time() + 31)


@pytest.fixture
def pipeline(store):
    browser = SimpleNamespace(settings=store.settings, open=AsyncMock(), guard=AsyncMock())
    engine = Engine(store, browser, set(), True)
    result = NativePipeline(engine)
    result.bridge = SimpleNamespace(
        submit=AsyncMock(),
        ready=AsyncMock(return_value=True),
        poll=AsyncMock(),
        forget=AsyncMock(),
        upload=AsyncMock(),
    )
    return result


async def test_slow_ack_allows_other_sender_but_not_same_sender(pipeline):
    store = pipeline.store
    ready(store, "a", "one")
    ready(store, "b", "one")
    ready(store, "c", "two")
    plan(store)
    assert await pipeline.dispatch_once()
    await asyncio.sleep(0.005)
    assert await pipeline.dispatch_once()
    assert len(pipeline.inflight) == 2
    assert {entry["job"]["sender"] for entry in pipeline.inflight.values()} == {"one", "two"}
    await asyncio.sleep(0.005)
    assert not await pipeline.dispatch_once()
    assert not pipeline.engine.ui.locked()


def receipt(entry, **changes):
    return {
        "id": "server-" + entry["row"]["client_id"],
        "client_id": entry["row"]["client_id"],
        "conversation": entry["job"]["conversation"],
        "from_me": True,
        "type": 7,
        "flight": 4,
        "server_status": 0,
        "data": {"text": entry["payload"]["text"]},
    } | changes


async def test_event_receipt_commits_before_forgetting_page_state(pipeline):
    ready(pipeline.store)
    plan(pipeline.store)
    await pipeline.dispatch_once()
    entry = next(iter(pipeline.inflight.values()))
    pipeline.bridge.poll.return_value = [{"client_id": entry["row"]["client_id"], "row": receipt(entry), "attempts": 1}]
    await pipeline.observe_once()
    assert not pipeline.inflight
    assert pipeline.store.list_jobs()[0]["state"] == "sent"
    assert pipeline.store.outbound_part(entry["job"], 0)["state"] == "acked"
    pipeline.bridge.forget.assert_awaited_once()


async def test_restricted_server_acceptance_is_not_delivery_and_is_never_retried(pipeline):
    ready(pipeline.store)
    plan(pipeline.store)
    assert await pipeline.dispatch_once()
    entry = next(iter(pipeline.inflight.values()))
    server = receipt(entry, delivery_restricted=True, delivery_code=8101)
    pipeline.bridge.poll.return_value = [{"client_id": entry["row"]["client_id"], "row": server, "attempts": 1}]
    await pipeline.observe_once()
    job = pipeline.store.list_jobs()[0]
    assert job["state"] == "failed" and job["error"] == "platform_delivery_restricted:8101"
    row = pipeline.store.outbound_part(job, 0)
    assert row["state"] == "rejected" and row["server_id"] == server["id"]
    assert pipeline.store.parts(job)[0]["state"] == "rejected"
    assert pipeline.store.get_meta("paused") == "platform_delivery_restricted"
    pipeline.store.recover(time.time())
    pipeline.store.set_meta("paused", "")
    assert not await pipeline.dispatch_once()
    assert pipeline.bridge.submit.await_count == 1


@pytest.mark.parametrize("wrong", [{"conversation": "other"}, {"client_id": "other"}, {"data": {"text": "other"}}])
async def test_mismatched_receipt_pauses_without_resubmission(pipeline, wrong):
    ready(pipeline.store)
    plan(pipeline.store)
    await pipeline.dispatch_once()
    entry = next(iter(pipeline.inflight.values()))
    pipeline.bridge.poll.return_value = [{"client_id": entry["row"]["client_id"], "row": receipt(entry, **wrong)}]
    await pipeline.observe_once()
    assert pipeline.store.list_jobs()[0]["state"] == "uncertain"
    assert pipeline.store.get_meta("paused") == "delivery_requires_review"
    assert not await pipeline.dispatch_once()
    assert pipeline.bridge.submit.await_count == 1


async def test_slow_upload_does_not_own_browser_ui(pipeline):
    job = ready(pipeline.store, parts=[{"files": ["x"], "md5": ["x"]}])
    plan(pipeline.store)
    release = asyncio.Event()
    started = asyncio.Event()

    async def upload(row, files):
        started.set()
        await release.wait()
        return {"handle": "x", "generation": "page"}

    pipeline.files = AsyncMock(return_value=[])
    pipeline.bridge.upload.side_effect = upload
    task = asyncio.create_task(pipeline.upload_once())
    await asyncio.wait_for(started.wait(), 1)
    async with pipeline.engine.ui:
        assert not task.done()
    release.set()
    await task
    assert pipeline.store.outbound_part(job, 0)["state"] == "uploaded"


async def test_gallery_forward_fallback_records_intent_and_receipt(pipeline):
    ready(pipeline.store, parts=[{"forward_source": "original", "md5": ["image"]}])
    plan(pipeline.store)
    pipeline.browser.send = AsyncMock(return_value=["forward-receipt"])
    pipeline.bridge.install = AsyncMock()
    assert await pipeline.dispatch_once()
    job = pipeline.store.list_jobs()[0]
    assert job["state"] == "sent"
    row = pipeline.store.outbound_part(job, 0)
    assert row["state"] == "acked" and row["server_id"] == "forward-receipt"
    assert float(pipeline.store.get_meta("next_send_at")) > 0


async def test_gallery_forward_timeout_is_not_retried_after_restart(pipeline):
    ready(pipeline.store, parts=[{"forward_source": "original", "md5": ["image"]}])
    plan(pipeline.store)
    pipeline.browser.send = AsyncMock(side_effect=TimeoutError())
    assert await pipeline.dispatch_once()
    job = pipeline.store.list_jobs()[0]
    assert job["state"] == "uncertain"
    assert pipeline.store.outbound_part(job, 0)["state"] == "submitted"
    pipeline.store.recover(time.time())
    assert not await pipeline.dispatch_once()
    assert pipeline.browser.send.await_count == 1


def test_pause_between_claim_and_submission_does_not_create_intent(store):
    ready(store)
    plan(store)
    job = store.claim("ready", time.time())
    row = store.outbound_part(job, 0)
    store.set_meta("paused", "delivery_requires_review")
    with pytest.raises(RuntimeError, match="暂停状态"):
        store.begin_outbound(job, row, time.time())
    assert store.outbound_part(job, 0)["state"] == "uploaded"
    assert store.parts(job)[0]["state"] == "pending"
