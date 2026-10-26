"""视频选择的去重、发送者绑定、重启恢复与缓存保护。"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from qiuyuantu_inbox.choices import parse_selection
from qiuyuantu_inbox.models import Message, Settings, message_from_snapshot
from qiuyuantu_inbox.store import Store

MANIFEST = {
    "files": ["artifacts/source/video.mp4"],
    "md5": ["video"],
    "details": [{"format": "MP4"}],
    "title": "测试视频",
}


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "queue", Settings())
    result.bind("account", 1000)
    return result


def offer(store, number="1", sender="sender", started=1005):
    message = Message(number, "chat", sender, started - 4, "work", {"work_id": "123"})
    assert store.ingest(message, started - 3) == "queued"
    job = store.claim("queued", started - 2)
    store.ready(job, "source", [{"offer_video": MANIFEST}], started - 1)
    sending = store.claim("ready", started)
    store.start_part(sending, 0, started)
    store.finish_part(sending, 0, ["prompt-" + number])
    store.finish(sending, "sent", started + 1)
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM video_choices WHERE job=?", (message.key,)).fetchone())


def selection(number="choice", mode="gif", code="", sender="sender", conversation="chat", created=1010):
    return Message(number, conversation, sender, created, "selection", {"mode": mode, "code": code})


@pytest.mark.parametrize(
    ("text", "mode", "code"),
    [
        ("1", "video", ""),
        ("原视频", "video", ""),
        ("GIF", "gif", ""),
        ("LIVE", "video", ""),
        ("实况", "video", ""),
        ("2️⃣", "gif", ""),
        ("都要", "both", ""),
        ("３", "both", ""),
        ("2 #a1b2c3d4", "gif", "A1B2C3D4"),
        ("原视频 11223344", "video", "11223344"),
        ("2 a1b2", "gif", "A1B2"),
        ("8910B18B", None, "8910B18B"),
        ("#8910", None, "8910"),
    ],
)
def test_selection_text_is_explicit_and_supports_keycap_digits(text, mode, code):
    assert parse_selection(text) == {"mode": mode, "code": code}


@pytest.mark.parametrize("text", ["你好", "123", "gif anything", "GIF https://example.com", "都要坏编号", "ABCD12345"])
def test_other_text_is_not_a_selection(text):
    assert parse_selection(text) is None


def test_selection_input_never_accepts_own_messages_or_invalid_time():
    raw = {
        "id": "1",
        "conversation": "chat",
        "sender": "sender",
        "created": 1001,
        "kind": "text",
        "data": {"text": "2"},
    }
    assert message_from_snapshot(raw).kind == "selection"
    assert message_from_snapshot(raw | {"from_me": True}) is None
    assert message_from_snapshot(raw | {"created": float("nan")}) is None


def test_offer_waits_after_ack_and_preserves_prompt_receipt(store):
    choice = offer(store)
    assert store.status(1007)["counts"] == {"waiting": 1}
    assert choice["expires"] == 1006 + store.settings.choice_seconds
    assert store.select_video(selection(), 1011) == "selected"
    job = store.claim("queued", 1012)
    assert store.selection(job)["mode"] == "gif"
    assert store.parts(job)[0]["receipt"] == '["prompt-1"]'
    store.ready(job, "gif", [{"files": ["clip.gif"], "md5": ["gif"]}], 1013)
    assert [part["number"] for part in store.parts(job)] == [0, 1]
    assert store.parts(job)[0]["state"] == "sent"


def test_repeated_decision_cannot_requeue_a_selected_video(store):
    offer(store)
    command = selection()
    assert store.select_video(command, 1011) == "selected"
    assert store.select_video(command, 1012) == "duplicate"
    assert store.select_video(selection("second", "both"), 1013) == "unmatched"
    job = store.claim("queued", 1014)
    assert store.selection(job)["mode"] == "gif"


@pytest.mark.parametrize("changes", [{"sender": "other"}, {"conversation": "other"}])
def test_known_code_cannot_select_another_senders_video(store, changes):
    choice = offer(store)
    assert store.select_video(replace(selection(code=choice["code"]), **changes), 1011) == "unmatched"
    assert store.status(1012)["counts"] == {"waiting": 1}


def test_multiple_waiting_videos_require_code_and_hint_is_rate_limited(store):
    offer(store)
    second = offer(store, "2")
    assert store.select_video(selection(), 1011) == "ambiguous"
    assert store.select_video(selection("again"), 1012) == "ambiguous"
    assert store.status(1013)["counts"] == {"waiting": 2, "ready": 1}
    assert store.select_video(selection("numbered", code=second["code"]), 1014) == "selected"


def test_ambiguous_format_then_code_survives_restart_and_selects_once(store):
    offer(store)
    second = offer(store, "2")
    assert store.select_video(selection(), 1011) == "ambiguous"
    recovered = Store(store.root, store.settings)
    command = message_from_snapshot(
        {
            "id": "code-only",
            "conversation": "chat",
            "sender": "sender",
            "created": 1012,
            "kind": "text",
            "data": {"text": second["code"][:4]},
        }
    )
    recovered.capture([command], 1013)
    assert recovered.handle_inbound(recovered.inbound()[0], 1013) == "selected"
    assert recovered.inbound() == []
    assert recovered.select_video(command, 1014) == "duplicate"
    assert recovered.selection({"id": second["job"]})["mode"] == "gif"
    repeated = selection("another-code", mode=None, code=second["code"], created=1015)
    assert recovered.select_video(repeated, 1015) == "unmatched"


def test_code_alone_never_assumes_a_format(store):
    choice = offer(store)
    assert store.select_video(selection(mode=None, code=choice["code"]), 1011) == "unmatched"
    assert store.selection({"id": choice["job"]}) is None
    assert store.status(1012)["counts"] == {"waiting": 1}


@pytest.mark.parametrize("changes", [{"sender": "other"}, {"conversation": "other"}, {"created": 1009}])
def test_code_followup_is_bound_to_sender_conversation_and_time(store, changes):
    offer(store)
    second = offer(store, "2")
    assert store.select_video(selection(), 1011) == "ambiguous"
    command = selection("code-only", mode=None, code=second["code"], created=1012)
    assert store.select_video(replace(command, **changes), 1013) == "unmatched"
    assert store.selection({"id": second["job"]}) is None


def test_code_followup_cannot_select_newer_or_expired_requests(store):
    offer(store)
    offer(store, "2")
    third = offer(store, "3", started=1020)
    assert store.select_video(selection(), 1025) == "ambiguous"
    command = selection("newer-code", mode=None, code=third["code"], created=1026)
    assert store.select_video(command, 1026) == "unmatched"
    assert store.select_video(replace(command, id="expired", created=1610), 1611) == "unmatched"
    assert store.selection({"id": third["job"]}) is None


def test_short_code_collision_requires_more_digits(store):
    first = offer(store)
    second = offer(store, "2")
    with store.transaction() as db:
        db.execute("UPDATE video_choices SET code='ABCD1111' WHERE job=?", (first["job"],))
        db.execute("UPDATE video_choices SET code='ABCD2222' WHERE job=?", (second["job"],))
    assert store.select_video(selection(code="ABCD"), 1011) == "ambiguous"
    with store.connect() as db:
        hint = json.loads(db.execute("SELECT payload FROM parts WHERE job=?", (selection().key,)).fetchone()[0])["text"]
    assert "ABCD1111" in hint and "ABCD2222" in hint and "测试视频" in hint
    assert store.select_video(selection("longer", mode=None, code="ABCD2", created=1012), 1013) == "selected"
    assert store.selection({"id": second["job"]})["mode"] == "gif"


def test_repeated_ambiguous_format_updates_even_when_notice_is_limited(store):
    offer(store)
    second = offer(store, "2")
    assert store.select_video(selection(), 1011) == "ambiguous"
    assert store.select_video(selection("change-format", mode="both", created=1012), 1013) == "ambiguous"
    assert store.select_video(selection("code-only", mode=None, code=second["code"], created=1014), 1015) == "selected"
    assert store.selection({"id": second["job"]})["mode"] == "both"


def test_late_old_reply_cannot_replace_a_newer_format(store):
    offer(store)
    second = offer(store, "2")
    assert store.select_video(selection("newer", mode="both", created=1012), 1013) == "ambiguous"
    assert store.select_video(selection("late", mode="gif", created=1010), 1014) == "ambiguous"
    assert store.select_video(selection("code-only", mode=None, code=second["code"], created=1015), 1016) == "selected"
    assert store.selection({"id": second["job"]})["mode"] == "both"


def test_short_code_reply_waits_for_inflight_prompt_receipt(store):
    choice = offer(store)
    with store.transaction() as db:
        db.execute("UPDATE jobs SET state='sending' WHERE id=?", (choice["job"],))
        db.execute("UPDATE video_choices SET expires=NULL WHERE job=?", (choice["job"],))
    command = selection(code=choice["code"][:4])
    assert store.select_video(command, 1011) == "deferred"
    store.recover(1012)
    assert store.select_video(command, 1013) == "selected"


def test_later_prompt_does_not_make_earlier_reply_ambiguous(store):
    first = offer(store)
    offer(store, "2", started=1020)
    assert store.select_video(selection(created=1010), 1025) == "selected"
    with store.connect() as db:
        assert db.execute("SELECT mode FROM video_choices WHERE job=?", (first["job"],)).fetchone()[0] == "gif"


def test_reply_before_prompt_is_not_replayed_against_future_video(store):
    command = selection(created=1001)
    assert store.select_video(command, 1002) == "unmatched"
    offer(store, started=1005)
    assert store.select_video(command, 1011) == "duplicate"
    assert store.select_video(selection("different-id", created=1001), 1011) == "unmatched"


def test_concurrent_decisions_apply_only_once(store):
    offer(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda number: store.select_video(selection(str(number)), 1011), [1, 2]))
    assert sorted(result) == ["selected", "unmatched"]


def test_waiting_choice_survives_restart_and_conversion_failure_keeps_prompt(store):
    choice = offer(store)
    store.recover(1010)
    assert store.status(1010)["counts"] == {"waiting": 1}
    assert store.select_video(selection(), 1011) == "selected"
    job = store.claim("queued", 1012)
    store.preparation_failed(job, "gif_failed", 1013, retryable=False)
    assert [part["number"] for part in store.parts(job)] == [0, 1]
    assert json.loads(store.parts(job)[0]["receipt"]) == ["prompt-1"]
    assert store.selection(job)["code"] == choice["code"]


def test_expiry_cancels_once_and_never_sends_video(store):
    choice = offer(store)
    assert store.select_video(selection(created=1610), 1610) == "unmatched"
    store.expire_choices(choice["expires"])
    store.expire_choices(choice["expires"] + 1)
    job = store.claim("ready", 1700)
    parts = store.parts(job)
    assert len(parts) == 2 and parts[0]["state"] == "sent"
    assert "过期" in json.loads(parts[1]["payload"])["text"]
    store.start_part(job, 1, 1701)
    store.finish_part(job, 1, ["expired-notice"])
    store.finish(job, "sent", 1702)
    assert store.status(1703)["counts"] == {"failed": 1}


def test_source_cache_is_pinned_while_job_points_to_gif_cache(store):
    offer(store)
    store.cache("source", MANIFEST, 12, 1)
    store.cache("gif", {}, 12, 1)
    assert store.select_video(selection(), 1011) == "selected"
    job = store.claim("queued", 1012)
    store.pin_artifact(job, "gif")
    assert store.expired_artifacts(100000, pressure=True) == []
    assert not store.remove_artifact("source")
    assert not store.remove_artifact("gif")


def test_recovery_after_prompt_ack_restores_waiting_not_prompt_resend(store):
    choice = offer(store)
    with store.transaction() as db:
        db.execute("UPDATE jobs SET state='sending' WHERE id=?", (choice["job"],))
        db.execute("UPDATE video_choices SET expires=NULL WHERE job=?", (choice["job"],))
    store.recover(1010)
    assert store.status(1010)["counts"] == {"waiting": 1}
    assert store.claim("ready", 1011) is None


def test_waiting_uses_quota_but_decisions_remain_available(store):
    choice = offer(store)
    store.settings = replace(store.settings, max_pending=1, sender_pending=1)
    assert store.select_video(selection(code=choice["code"]), 1011) == "selected"
    assert store.claim("queued", 1012) is not None


def test_scope_expansion_keeps_previously_received_selection_usable(store):
    choice = offer(store)
    store.configure_scope("pilot", 1000)
    store.set_meta("pilot_scope", '["chat"]')
    command = selection(code=choice["code"])
    store.capture([command], 1011)
    store.configure_scope("all", 1012)
    assert store.handle_inbound(store.inbound()[0], 1013) == "selected"
    assert store.selection({"id": choice["job"]})["mode"] == "gif"
    assert store.parts({"id": choice["job"]})[0]["state"] == "sent"


def test_uncertain_prompt_requires_review_before_accepting_selection(store):
    choice = offer(store)
    with store.transaction() as db:
        db.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (choice["job"],))
        db.execute("UPDATE parts SET state='sending',receipt=NULL WHERE job=?", (choice["job"],))
        db.execute("UPDATE video_choices SET expires=NULL WHERE job=?", (choice["job"],))
    assert store.select_video(selection(), 1011) == "deferred"
    store.recover(1012)
    assert store.get_meta("paused") == "delivery_requires_review"
    store.resolve(choice["job"], "delivered", 1013)
    assert store.status(1013)["counts"] == {"waiting": 1}
    assert store.select_video(selection(), 1014) == "selected"
