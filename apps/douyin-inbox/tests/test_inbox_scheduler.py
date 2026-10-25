"""多用户轮转、发送片段顺序、成本修正和依赖等待的离线负载验证。"""

from dataclasses import replace

import pytest

from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.store import Store


def enqueue(store, sender, number, parts=1, control=False):
    message = Message(str(number), f"chat-{sender}", sender, 1001, "work", {"work_id": "123"})
    assert store.ingest(message, 1002 + number / 1000) == "queued"
    job = store.claim("queued", 2000)
    assert job["id"] == message.key
    payload = {"text": "notice"} if control else {"files": ["image.jpg"], "md5": ["image"]}
    store.ready(job, "", [payload] * parts, 2001)
    return message.key


def send_part(store, now=3000):
    job = store.claim("ready", now)
    if not job:
        return None
    part = next(part for part in store.parts(job) if part["state"] != "sent")
    store.start_part(job, part["number"], now)
    store.finish_part(job, part["number"], [f"receipt-{job['id']}-{part['number']}"])
    remaining = any(part["state"] != "sent" for part in store.parts(job))
    store.finish(job, "ready" if remaining else "sent", now + 1)
    return job["sender"], job["id"], part["number"]


@pytest.mark.parametrize("users", [10, 50, 100])
def test_large_gallery_does_not_block_other_users_and_all_requests_finish(tmp_path, users):
    store = Store(tmp_path / "queue", Settings(sender_hourly=1000))
    store.bind("account", 1000)
    large = enqueue(store, "large", 0, parts=10)
    for i in range(1, users):
        enqueue(store, f"user-{i}", i)
    result = []
    while value := send_part(store):
        result.append(value)
    large_parts = [number for _, job, number in result if job == large]
    assert large_parts == list(range(10))
    assert len({sender for sender, _, _ in result[:users]}) == users
    assert store.status(4000)["counts"] == {"sent": users}


def test_only_one_preparation_per_sender_but_other_senders_keep_running(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    for number, sender in [(1, "a"), (2, "a"), (3, "b")]:
        store.ingest(Message(str(number), sender, sender, 1001, "work", {"work_id": "123"}), 1002)
    first = store.claim("queued", 1003)
    second = store.claim("queued", 1003)
    assert {first["sender"], second["sender"]} == {"a", "b"}
    assert store.claim("queued", 1003) is None


def test_control_messages_have_bounded_priority(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    enqueue(store, "media", 0)
    for i in range(1, 10):
        enqueue(store, f"notice-{i}", i, control=True)
    assert [send_part(store)[0] for _ in range(3)][2] == "media"


def test_shared_asset_waiter_does_not_consume_preparation_or_retry_budget(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    for i in range(3):
        store.ingest(Message(str(i), str(i), str(i), 1001, "work", {"work_id": "123"}), 1002)
    owner, waiter = store.claim("queued", 1003), store.claim("queued", 1003)
    assert store.acquire_asset(owner, "shared", 1004)
    assert not store.acquire_asset(waiter, "shared", 1004)
    assert store.claim("queued", 1005)["sender"] not in {owner["sender"], waiter["sender"]}
    assert store.status(1005)["counts"] == {"processing": 2, "blocked": 1}
    store.release_asset(owner, "shared", 1006)
    resumed = store.claim("queued", 1007)
    assert resumed["id"] == waiter["id"] and resumed["attempts"] == 1
    store.recover(1008)
    assert store.status(1008)["counts"] == {"queued": 3}


def test_fairness_and_active_output_order_survive_restart(tmp_path):
    store = Store(tmp_path / "queue", replace(Settings(), sender_hourly=100))
    store.bind("account", 1000)
    first = enqueue(store, "a", 1, parts=3)
    assert send_part(store)[1:] == (first, 0)
    second = enqueue(store, "a", 2)
    other = enqueue(store, "b", 3)
    store = Store(store.root, store.settings)
    output = [send_part(store) for _ in range(4)]
    assert other in [value[1] for value in output[:2]]
    assert [job for _, job, _ in output if job != other] == [first, first, second]


def test_older_preparation_cannot_interrupt_already_started_newer_output(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    older = Message("1", "chat-a", "a", 1001, "work", {"work_id": "123"})
    store.ingest(older, 1002)
    old_job = store.claim("queued", 1003)
    store.preparation_failed(old_job, "TimeoutError", 1999)
    new_message = Message("4", "chat-a", "a", 2000, "work", {"work_id": "123"})
    store.ingest(new_message, 2000)
    new_job = store.claim("queued", 2001)
    store.ready(new_job, "", [{"files": ["new.jpg"], "md5": ["new"]}] * 2, 2002)
    newer = new_message.key
    assert send_part(store)[1:] == (newer, 0)
    old_job = store.claim("queued", 4000)
    store.ready(old_job, "", [{"files": ["old.jpg"], "md5": ["old"]}], 4001)
    assert send_part(store, 4002)[1:] == (newer, 1)
    assert send_part(store, 4003)[1] == older.key
