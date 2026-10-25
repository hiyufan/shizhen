"""队列重启、并发入队与不确定发送不能造成重复回传。"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.store import LeaseLost, Store


def message(number="1", sender="sender"):
    return Message(number, f"chat-{sender}", sender, 1001, "work", {"work_id": "123", "photos": True})


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "inbox", Settings())
    result.bind("account", 1000)
    return result


def ready(store, count=1):
    store.ingest(message(), 1002)
    job = store.claim("queued", 1003)
    store.ready(job, "cache", [{"text": str(i)} for i in range(count)], 1004)
    return store.claim("ready", 1005)


def test_startup_baseline_does_not_reply_to_old_history(store):
    old = replace(message(), created=999)
    assert store.ingest(old, 1002) == "old"
    assert store.claim("queued", 1003) is None


def test_queue_and_sender_limits_are_persisted(store):
    assert store.ingest(message("1"), 1002) == "queued"
    assert store.ingest(message("2"), 1002) == "queued"
    assert store.ingest(message("3"), 1002) == "queued"
    assert store.ingest(message("4"), 1002) == "ready"
    assert store.ingest(message("5"), 1002) == "rejected"
    assert store.ingest(message("1"), 1002) == "duplicate"
    assert store.status(1003)["counts"] == {"queued": 3, "ready": 1, "rejected": 1}


def test_account_switch_cannot_reuse_outbox(store):
    with pytest.raises(RuntimeError, match="账号已变化"):
        store.bind("other-account", 2000)
    assert store.get_meta("account") == "account"
    assert float(store.get_meta("start_at")) == 1000


def test_scope_expansion_skips_pilot_history_but_does_not_reset_on_restart(store):
    store.configure_scope("pilot", 1000)
    store.configure_scope("all", 2000)
    assert store.ingest(message(), 2001) == "old"
    store.configure_scope("all", 3000)
    assert float(store.get_meta("start_at")) == 2000


def test_public_scope_preserves_existing_conversations_and_filters_new_history(store):
    store.configure_scope("pilot", 1000)
    store.set_meta("pilot_scope", json.dumps(["chat-existing", "chat-new"]))
    store.set_meta("start_at:chat-new", "1400")
    store.configure_scope("all", 2000)
    for sender, floor in [("existing", 1000), ("new", 1400), ("public", 2000)]:
        assert store.message_floor("chat-" + sender) == floor
        assert store.ingest(replace(message(sender, sender), created=floor), 2001) == "queued"
    assert store.ingest(replace(message("history", "public"), created=1999), 2001) == "old"
    recovered = Store(store.root, store.settings)
    recovered.configure_scope("all", 3000)
    assert recovered.message_floor("chat-existing") == 1000
    assert recovered.message_floor("chat-new") == 1400
    assert recovered.message_floor("chat-public") == 2000


def test_added_pilot_conversation_has_its_own_persistent_message_floor(store):
    store.set_meta("start_at:chat-new", "2000")
    old = message("existing", "existing")
    assert store.ingest(old, 2001) == "queued"
    assert store.ingest(replace(message("history", "new"), created=1999), 2001) == "old"
    assert store.ingest(replace(message("recent", "new"), created=2000), 2001) == "queued"
    recovered = Store(store.root, store.settings)
    assert recovered.cursor("chat-new") == 2000
    assert recovered.message_floor("chat-existing") == 1000
    assert recovered.ingest(replace(message("older", "new"), created=1990), 2002) == "old"


@pytest.mark.parametrize("kind", ["selection", "control"])
def test_added_conversation_does_not_replay_old_commands(store, kind):
    store.set_meta("start_at:chat-new", "2000")
    data = {"mode": "both", "code": "1234ABCD"} if kind == "selection" else {"action": "取消", "code": "1234ABCD"}
    command = replace(message("command", "new"), kind=kind, data=data)
    operation = store.select_video if kind == "selection" else store.control
    assert operation(command, 2001) == "old"
    assert store.status(2001)["counts"] == {}


def test_narrowed_scope_cannot_claim_other_users_jobs(store):
    store.ingest(message("1", "a"), 1002)
    store.ingest(message("2", "b"), 1002)
    job = store.claim("queued", 1003, {"chat-b"})
    assert job["sender"] == "b"
    assert store.claim("queued", 1003, {"chat-b"}) is None


def test_timings_accumulate_retries_without_exposing_messages(store):
    store.ingest(message(), 1002)
    job = store.claim("queued", 1005)
    store.measure(job, "prepare", 2)
    store.measure(job, "prepare", 4)
    timings = store.status()["timings"]
    assert timings["prepare_wait"]["average_seconds"] == 3
    assert timings["receive_lag"]["average_seconds"] == 1
    assert timings["prepare"] == {
        "samples": 1,
        "average_seconds": 6,
        "max_seconds": 6,
        "p50_seconds": 6,
        "p95_seconds": 6,
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0])
def test_settings_reject_nonfinite_or_nonpositive_timeouts(value):
    with pytest.raises(ValueError):
        replace(Settings(), connect_timeout=value).validate()


def test_recovered_preparation_uses_new_lease(store):
    store.ingest(message(), 1002)
    first = store.claim("queued", 1003)
    store.recover(1004)
    second = store.claim("queued", 1005)
    assert second["token"] != first["token"]
    with pytest.raises(LeaseLost):
        store.ready(first, "cache", [{"text": "stale"}], 1006)
    store.ready(second, "cache", [{"text": "current"}], 1006)


def test_send_without_external_attempt_is_safe_to_recover(store):
    ready(store)
    store.recover(1006)
    assert store.status(1007)["counts"] == {"ready": 1}


def test_restart_preserves_acknowledged_batches_and_stops_unknown_batch(store):
    job = ready(store, count=3)
    store.start_part(job, 0, 1006)
    store.finish_part(job, 0, ["server-message-1"])
    store.start_part(job, 1, 1007)
    store.recover(1008)
    assert store.status(1009)["counts"] == {"uncertain": 1}
    assert store.get_meta("paused") == "delivery_requires_review"
    assert store.claim("ready", 1009) is None
    store.resolve(job["id"], "delivered", 1010)
    resumed = store.claim("ready", 1011)
    assert [part["state"] for part in store.parts(resumed)] == ["sent", "sent", "pending"]


def test_all_acknowledged_batches_are_not_resent_after_restart(store):
    job = ready(store)
    store.start_part(job, 0, 1006)
    store.finish_part(job, 0, ["message-1"])
    store.recover(1007)
    assert store.status(1008)["counts"] == {"sent": 1}


def test_recovery_of_failure_notice_keeps_failed_result(store):
    store.ingest(message(), 1002)
    job = store.claim("queued", 1003)
    store.preparation_failed(job, "UnsupportedMedia", 1004, retryable=False)
    job = store.claim("ready", 2000)
    store.start_part(job, 0, 2001)
    store.finish_part(job, 0, ["notice"])
    store.recover(2002)
    assert store.status(2003)["counts"] == {"failed": 1}


def test_permanent_preparation_failure_queues_user_notice(store):
    store.ingest(message(), 1002)
    job = store.claim("queued", 1003)
    store.preparation_failed(job, "UnsupportedMedia", 1004, retryable=False)
    notice = store.claim("ready", 2000)
    assert notice["attempts"] == 1
    assert "text" in store.parts(notice)[0]["payload"]
    store.start_part(notice, 0, 2001)
    store.finish_part(notice, 0, ["message-1"])
    store.finish(notice, "sent", 2002)
    assert store.status(2003)["counts"] == {"failed": 1}


def test_transient_preparation_has_bounded_retries(store):
    store.ingest(message(), 1002)
    for attempt in range(1, 4):
        job = store.claim("queued", 10000 * attempt)
        store.preparation_failed(job, "TimeoutError", 10000 * attempt)
    assert store.claim("queued", 999999) is None
    assert store.claim("ready", 999999)["attempts"] == 3


def test_concurrent_duplicate_receipts_remain_unique_and_bounded(tmp_path):
    settings = replace(Settings(), max_pending=300, sender_pending=300)
    store = Store(tmp_path / "burst", settings)
    store.bind("account", 1000)
    events = [message(str(i), f"sender-{i}") for i in range(1000)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda event: store.ingest(event, 1002), events + events))
    counts = store.status(1003)["counts"]
    assert counts == {"queued": 280, "rejected": 720}


def test_referenced_artifact_is_not_deleted_while_delivery_waits(store):
    ready(store)
    store.cache("cache", {"files": ["x"]}, 100, 1)
    assert store.expired_artifacts(10000) == []


def test_settings_reject_contradictory_limits():
    with pytest.raises(ValueError):
        replace(Settings(), job_timeout=500).validate()
    with pytest.raises(ValueError):
        replace(Settings(), video_bytes=1000 << 20).validate()
