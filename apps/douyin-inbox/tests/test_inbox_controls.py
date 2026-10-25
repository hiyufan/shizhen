"""编号命令隔离、准备中取消、独立限频及控制容量。"""

from dataclasses import replace

import pytest

from qiuyuantu_inbox.models import Message, Settings, message_from_snapshot
from qiuyuantu_inbox.store import Store


@pytest.fixture
def waiting(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    message = Message("1", "chat", "sender", 1001, "work", {"work_id": "123"})
    store.ingest(message, 1002)
    job = store.claim("queued", 1003)
    store.ready(job, "", [{"offer_video": {"files": ["video.mp4"], "md5": ["video"]}}], 1004)
    job = store.claim("ready", 1005)
    store.start_part(job, 0, 1005)
    store.finish_part(job, 0, ["prompt"])
    store.finish(job, "sent", 1006)
    with store.connect() as db:
        code = db.execute("SELECT code FROM video_choices").fetchone()[0]
    return store, job, code


def command(code, number="cmd", action="取消", sender="sender"):
    return Message(number, "chat", sender, 1010, "control", {"code": code, "action": action})


def test_cancel_requires_same_sender_and_is_idempotent(waiting):
    store, job, code = waiting
    assert store.control(command(code, number="other-command", sender="other"), 1011) == "unmatched"
    assert store.status(1011)["counts"] == {"waiting": 1}
    assert store.control(command(code), 1012) == "waiting"
    assert store.control(command(code), 1013) == "duplicate"
    assert store.status(1013)["counts"] == {"cancelled": 1, "ready": 1}


@pytest.mark.parametrize("restart", [False, True])
def test_cancel_during_preparation_never_commits_outputs_and_survives_restart(waiting, restart):
    store, job, code = waiting
    select = Message("choice", "chat", "sender", 1007, "selection", {"mode": "gif", "code": code})
    assert store.select_video(select, 1008) == "selected"
    working = store.claim("queued", 1009)
    assert store.control(command(code), 1011) == "processing"
    if restart:
        store.recover(1012)
    else:
        store.ready(working, "gif", [{"files": ["gif.gif"], "md5": ["digest"]}], 1012)
    assert store.status(1012)["counts"] == {"cancelled": 1, "ready": 1}
    assert len(store.parts(job)) == 1


def test_control_limit_is_independent_from_parse_limit(waiting):
    store, _, code = waiting
    store.settings = replace(store.settings, commands_per_minute=2)
    assert store.control(command(code, "p1", "进度"), 1011) == "waiting"
    assert store.control(command(code, "p2", "进度"), 1012) == "waiting"
    assert store.control(command(code, "p3", "进度"), 1013) == "limited"
    raw = {
        "id": "4",
        "conversation": "chat",
        "sender": "sender",
        "created": 1010,
        "kind": "text",
        "data": {"text": f"取消 {code}"},
    }
    assert message_from_snapshot(raw).kind == "control"
