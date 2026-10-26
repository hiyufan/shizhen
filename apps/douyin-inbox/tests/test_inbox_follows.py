"""自动回关的基线、持久去重、独立预算、未知提交与恢复。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.__main__ import parser, run
from qiuyuantu_inbox.follows import FollowBack
from qiuyuantu_inbox.models import Settings
from qiuyuantu_inbox.store import Store


def user(uid, following=0, follower=1, blocked=False):
    return {
        "uid": str(uid),
        "sec_uid": "sec_" + str(uid),
        "following": following,
        "follower": follower,
        "blocked": blocked,
    }


def page(*users, cursor=0, more=False):
    return {"users": list(users), "cursor": cursor, "more": more}


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path, Settings())
    result.bind("100", 1000)
    return result


def baseline(store):
    store.record_follow_page(page(user(1)), 1001, store.follow_scan(1000))


def queued(store, *uids):
    baseline(store)
    store.record_follow_page(page(*(user(uid) for uid in uids)), 1002)


def rows(store):
    with store.connect() as db:
        return {r["uid"]: dict(r) for r in db.execute("SELECT * FROM follow_users")}


def test_baseline_requires_complete_scan_and_survives_restart(store):
    scan = store.follow_scan(1000)
    store.record_follow_page(page(user(1), cursor=900, more=True), 1001, scan)
    assert not store.follow_status()["baseline_complete"]
    recovered = Store(store.root, store.settings)
    assert recovered.follow_scan(1002) == {"cursor": 900, "ceiling": 1000}
    recovered.record_follow_page(page(user(2)), 1003, recovered.follow_scan(1002))
    assert recovered.follow_status()["baseline_complete"]
    assert recovered.claim_follow(1004) is None
    recovered.record_follow_page(page(user(1), user(2), user(3)), 1005)
    assert {uid: row["state"] for uid, row in rows(recovered).items()} == {
        "1": "baseline",
        "2": "baseline",
        "3": "queued",
    }


def test_duplicates_and_ineligible_users_do_not_create_follow_intents(store):
    baseline(store)
    batch = page(user(2), user(2), user(3, following=2), user(4, follower=0), user(5, blocked=True))
    store.record_follow_page(batch, 1002)
    store.record_follow_page(batch, 1003)
    assert store.follow_status()["counts"] == {"baseline": 1, "queued": 1, "skipped": 3}
    job = store.claim_follow(1004)
    assert store.begin_follow(job, 1004)
    store.finish_follow(job, "mutual", 1005)
    store.record_follow_page(batch, 1100)
    assert rows(store)["2"]["state"] == "mutual"
    assert store.claim_follow(1101) is None


def test_backpressure_rolls_back_page_and_checkpoint_together(store):
    baseline(store)
    store.settings = replace(store.settings, follow_pending=20)
    scan = store.follow_scan(1002)
    batch = page(*(user(uid) for uid in range(2, 23)), cursor=900, more=True)
    with pytest.raises(BufferError):
        store.record_follow_page(batch, 1003, scan)
    assert len(rows(store)) == 1
    assert not store.get_meta("follow_scan")


def test_stale_scan_checkpoint_cannot_commit_users(store):
    baseline(store)
    scan = store.follow_scan(1002)
    store.record_follow_page(page(user(2), cursor=900, more=True), 1003, scan)
    with pytest.raises(RuntimeError, match="游标"):
        store.record_follow_page(page(user(3)), 1004, scan)
    assert "3" not in rows(store)


def test_concurrent_claims_do_not_claim_same_user(store):
    queued(store, 2)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(store.claim_follow, [1003, 1003]))
    assert sum(job is not None for job in claimed) == 1


def test_interval_hourly_daily_budgets_survive_restart(store):
    queued(store, 2, 3, 4)
    store.settings = replace(store.settings, follow_interval=30, follow_hourly=1, follow_daily=2)
    job = store.claim_follow(1003)
    assert store.begin_follow(job, 1003)
    store.finish_follow(job, "mutual", 1004)
    recovered = Store(store.root, store.settings)
    assert recovered.claim_follow(1005) is None
    assert recovered.claim_follow(1034) is None
    job = recovered.claim_follow(4604)
    assert recovered.begin_follow(job, 4604)
    recovered.finish_follow(job, "mutual", 4605)
    assert recovered.claim_follow(8205) is None
    assert recovered.claim_follow(87404) is not None


def test_budget_is_checked_again_when_submission_begins(store):
    queued(store, 2, 3)
    first, second = store.claim_follow(1003), store.claim_follow(1003)
    assert store.begin_follow(first, 1004)
    assert not store.begin_follow(second, 1004)
    assert rows(store)[second["uid"]]["state"] == "queued"


def test_operator_pause_during_ui_wait_prevents_submission(store):
    queued(store, 2)
    job = store.claim_follow(1003)
    store.set_meta("paused", "operator")
    assert not store.begin_follow(job, 1004)
    assert rows(store)["2"]["submitted"] is None


def test_crash_before_and_after_intent_have_different_recovery(store):
    queued(store, 2, 3)
    before, after = store.claim_follow(1003), store.claim_follow(1003)
    assert store.begin_follow(after, 1004)
    recovered = Store(store.root, store.settings)
    recovered.recover_follows(1005)
    assert rows(recovered)[before["uid"]]["state"] == "queued"
    assert rows(recovered)[after["uid"]]["state"] == "uncertain"
    assert recovered.get_meta("follow_paused") == "outcome_unknown"
    assert not recovered.get_meta("paused")
    assert recovered.claim_follow(1100) is None
    recovered.set_meta("follow_paused", "")
    assert recovered.claim_follow(1100)["uid"] == before["uid"]


def test_old_worker_cannot_finish_reclaimed_follow_job(store):
    queued(store, 2)
    old = store.claim_follow(1003)
    store.recover_follows(1004)
    current = store.claim_follow(1005)
    with pytest.raises(RuntimeError):
        store.finish_follow(old, "mutual", 1006)
    assert store.begin_follow(current, 1007)


@pytest.mark.parametrize("reason", ["operator", "platform_rejected"])
def test_restart_and_reconciliation_never_clear_explicit_follow_pause(store, reason):
    queued(store, 2)
    job = store.claim_follow(1003)
    store.begin_follow(job, 1004)
    store.set_meta("follow_paused", reason)
    store.recover_follows(1005)
    assert store.get_meta("follow_paused") == reason
    store.finish_follow(job, "mutual", 1006)
    store.release_follow_pause()
    assert store.get_meta("follow_paused") == reason


@pytest.mark.parametrize("state", ["rejected", "uncertain"])
def test_follow_failures_only_pause_follow_queue(store, state):
    queued(store, 2)
    job = store.claim_follow(1003)
    assert store.begin_follow(job, 1004)
    store.finish_follow(job, state, 1005)
    assert store.get_meta("follow_paused")
    assert not store.get_meta("paused")
    assert store.follow_status()["counts"][state] == 1


@pytest.mark.parametrize(
    "changes", [{"follow_scan_pages": 21}, {"follow_pending": 19}, {"follow_hourly": 101}, {"follow_timeout": 300}]
)
def test_follow_settings_reject_inconsistent_limits(changes):
    with pytest.raises(ValueError):
        replace(Settings(), **changes).validate()


def workflow(store):
    engine = SimpleNamespace(
        store=store,
        settings=store.settings,
        browser=SimpleNamespace(),
        ui=asyncio.Lock(),
        stopped=asyncio.Event(),
        paused=AsyncMock(return_value=False),
    )
    worker = FollowBack(engine)
    worker.client = SimpleNamespace(
        call=AsyncMock(return_value=True), follow=AsyncMock(return_value={"state": "mutual"})
    )
    return worker


async def test_submit_timeout_preserves_intent_and_never_repeats_post(store):
    queued(store, 2)
    worker = workflow(store)
    worker.client.follow.side_effect = asyncio.TimeoutError
    with pytest.raises(asyncio.TimeoutError):
        await worker.submit_once()
    assert rows(store)["2"]["state"] == "uncertain"
    assert not store.get_meta("paused")
    await worker.submit_once()
    assert worker.client.follow.await_count == 1


async def test_busy_page_request_does_not_create_another_intent(store):
    queued(store, 2)
    worker = workflow(store)
    worker.installed = True
    worker.client.call.return_value = False
    await worker.submit_once()
    worker.client.follow.assert_not_awaited()
    assert rows(store)["2"]["state"] == "queued"


async def test_reconcile_unknown_only_reads_relation_and_retains_operator_pause(store):
    queued(store, 2)
    job = store.claim_follow(1003)
    store.begin_follow(job, 1004)
    store.finish_follow(job, "uncertain", 1005)
    worker = workflow(store)
    worker.client.call.return_value = user(2, following=0)
    await worker.reconcile()
    assert rows(store)["2"]["state"] == "uncertain"
    worker.client.call.return_value = user(2, following=2)
    store.set_meta("follow_paused", "operator")
    await worker.reconcile()
    assert rows(store)["2"]["state"] == "mutual"
    assert store.get_meta("follow_paused") == "operator"
    worker.client.follow.assert_not_awaited()


async def test_approved_private_follow_is_rechecked_until_mutual_without_another_post(store):
    queued(store, 2)
    job = store.claim_follow(1003)
    store.begin_follow(job, 1004)
    store.finish_follow(job, "requested", 1005)
    worker = workflow(store)
    worker.client.call.return_value = user(2, following=2)
    await worker.reconcile()
    assert rows(store)["2"]["state"] == "mutual"
    worker.client.follow.assert_not_awaited()


@pytest.mark.parametrize("arguments", [["run", "--all"], ["run", "--pilot-current", "--send"]])
async def test_auto_follow_requires_explicit_public_sending(store, arguments):
    store.settings = replace(store.settings, auto_follow=True)
    with pytest.raises(ValueError, match="--all --send"):
        await run(store, parser().parse_args(arguments))


async def test_full_discovery_queue_still_drains_existing_jobs(store):
    queued(store, 2)
    worker = workflow(store)
    worker.discover = AsyncMock(side_effect=BufferError)
    await worker.tick()
    assert rows(store)["2"]["state"] == "mutual"
    assert store.get_meta("follow_scan_error") == "queue_full"


async def test_sweep_continuation_also_checks_current_head(store):
    baseline(store)
    scan = store.follow_scan(1002)
    store.record_follow_page(page(user(2), cursor=900, more=True), 1003, scan)
    worker = workflow(store)
    worker.client.call.side_effect = [page(user(3), cursor=950, more=True), page(user(4))]
    await worker.discover()
    assert [c.args[0] for c in worker.client.call.await_args_list] == ["page", "page"]
    assert worker.client.call.await_args_list[1].kwargs["cursor"] == 900
    assert set(rows(store)) == {"1", "2", "3", "4"}
    assert not store.get_meta("follow_scan")


async def test_cancellation_after_durable_intent_recovers_as_unknown(store):
    queued(store, 2)
    worker = workflow(store)
    started = asyncio.Event()

    async def sending(job):
        started.set()
        await asyncio.Future()

    worker.client.follow.side_effect = sending
    task = asyncio.create_task(worker.submit_once())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rows(store)["2"]["state"] == "submitted"
    store.recover_follows(1010)
    assert rows(store)["2"]["state"] == "uncertain"
    assert json.loads(store.get_meta("follow_scan") or "null") is None
