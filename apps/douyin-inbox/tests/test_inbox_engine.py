"""收件数据边界、服务端回执和发送失败时的工作流。"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from qiuyuantu_inbox.browser import Browser, BrowserUnavailable, acknowledged
from qiuyuantu_inbox.engine import Engine
from qiuyuantu_inbox.media import UnsupportedMedia
from qiuyuantu_inbox.models import Message, Settings, content_key, message_from_snapshot
from qiuyuantu_inbox.store import Store


def event(**kwargs):
    raw = {
        "id": "message",
        "conversation": "chat",
        "sender": "sender",
        "created": 1001,
        "kind": "media",
        "data": {"url": "https://p11-sign.douyinpic.com/obj/sticker?sig=one"},
    }
    return raw | kwargs


def test_only_recognized_incoming_media_is_accepted():
    assert message_from_snapshot(event(from_me=True)) is None
    assert message_from_snapshot(event(id="")) is None
    assert message_from_snapshot(event(data={"url": "http://127.0.0.1/private"})) is None
    assert message_from_snapshot(event(data={"url": "https://douyinpic.com.evil.test/a"})) is None
    assert message_from_snapshot(event()) is not None


def test_plain_messages_are_ignored_but_share_links_are_accepted():
    assert message_from_snapshot(event(kind="text", data={"text": "你好"})) is None
    parsed = message_from_snapshot(event(kind="text", data={"text": "分享 https://v.douyin.com/example/"}))
    assert parsed.kind == "link"
    assert "text" not in parsed.data


def test_signed_url_rotation_does_not_duplicate_content_cache():
    first = message_from_snapshot(event())
    second = message_from_snapshot(event(data={"url": "https://p11-sign.douyinpic.com/obj/sticker?sig=two"}))
    assert content_key(first) == content_key(second)


def ack(identifier="new", **kwargs):
    return {
        "id": identifier,
        "conversation": "chat",
        "from_me": True,
        "flight": 4,
        "server_status": 0,
        "md5": "digest",
        "data": {},
    } | kwargs


def test_delivery_requires_new_server_ack_matching_recipient_and_file():
    part = {"md5": ["digest"]}
    assert acknowledged([ack()], part, "chat", set()) == ["new"]
    assert acknowledged([ack()], part, "chat", {"new"}) == []
    assert acknowledged([ack(conversation="other")], part, "chat", set()) == []
    assert acknowledged([ack(flight=1)], part, "chat", set()) == []
    assert acknowledged([ack(md5="other")], part, "chat", set()) == []
    assert acknowledged([ack()], {"md5": ["digest", "digest"]}, "chat", set()) == []
    assert acknowledged([ack("a"), ack("b")], {"md5": ["digest", "digest"]}, "chat", set()) == ["a", "b"]


def test_native_live_photo_ack_requires_video_reference():
    part = {"md5": ["digest"], "live_vid": "native_video"}
    assert acknowledged([ack()], part, "chat", set()) == []
    assert acknowledged([ack(live_vid="different")], part, "chat", set()) == []
    assert acknowledged([ack(live_vid="native_video")], part, "chat", set()) == ["new"]


def test_composed_live_receipt_requires_both_digests_and_native_type():
    part = {"md5": ["digest", "video"], "live_vid": "paired", "live_pair": True}
    row = ack(type=27, live_vid="paired", live_md5="video", awe_type=2704)
    assert acknowledged([row], part, "chat", set()) == ["new"]
    for changes in ({"awe_type": 2702}, {"live_md5": "other"}, {"md5": "other"}, {"type": 30}):
        assert acknowledged([row | changes], part, "chat", set()) == []


def test_video_receipt_requires_video_message_type():
    part = {"md5": ["digest"], "video": True}
    assert acknowledged([ack(type=27)], part, "chat", set()) == []
    assert acknowledged([ack(type=30)], part, "chat", set()) == ["new"]


def test_native_live_photo_input_preserves_video_without_exporting_keys():
    data = {"url": "https://p11.douyinpic.com/obj/a", "live_vid": "v01234", "md5": "a" * 32}
    parsed = message_from_snapshot(event(id="123456", kind="live", data=data))
    assert parsed.kind == "live"
    assert parsed.data["live_vid"] == "v01234"
    assert message_from_snapshot(event(id="123456", kind="live", data=data | {"live_vid": ""})) is None
    assert message_from_snapshot(event(id='123"bad', kind="live", data=data)) is None


def test_native_photo_does_not_require_downloading_encrypted_image():
    data = {"url": "https://p11.douyinpic.com/obj/a", "md5": "a" * 32}
    assert message_from_snapshot(event(id="123456", kind="photo", data=data)).kind == "photo"
    assert message_from_snapshot(event(id='123"bad', kind="photo", data=data)) is None
    assert message_from_snapshot(event(id="123456", kind="photo", data=data | {"md5": ""})) is None


@pytest.fixture
def engine(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    store.bind("account", 1000)
    browser = SimpleNamespace(
        open=AsyncMock(),
        guard=AsyncMock(),
        send=AsyncMock(return_value=["server-1"]),
        cached_work_media=AsyncMock(return_value=None),
        work_media=AsyncMock(return_value=None),
    )
    result = Engine(store, browser, set(), True)
    result.processor.describe = AsyncMock(return_value=None)
    return result


def queue_notice(engine):
    message = Message("1", "chat", "sender", 1001, "work", {"work_id": "123"})
    engine.store.ingest(message, 1002)
    job = engine.store.claim("queued", 1003)
    engine.store.ready(job, "", [{"text": "test"}], 1004)


async def drain_sends(engine):
    for _ in range(10):
        if not await engine.send_once():
            return
    raise AssertionError("发送未在片段数量范围内结束")


async def test_preflight_failure_does_not_become_uncertain(engine):
    queue_notice(engine)
    engine.browser.open.side_effect = BrowserUnavailable()
    assert await engine.send_once()
    assert engine.store.status()["counts"] == {"ready": 1}
    engine.browser.send.assert_not_awaited()


async def test_timeout_after_send_stops_automatic_retry(engine):
    queue_notice(engine)
    engine.browser.send.side_effect = TimeoutError()
    assert await engine.send_once()
    assert engine.store.status()["counts"] == {"uncertain": 1}
    engine.store.set_meta("paused", "")
    assert not await engine.send_once()
    assert engine.browser.send.await_count == 1


async def test_successful_send_stores_server_message_ids(engine):
    queue_notice(engine)
    assert await engine.send_once()
    job = engine.store.list_jobs()[0]
    assert job["state"] == "sent"
    assert json.loads(engine.store.parts(job)[0]["receipt"]) == ["server-1"]


async def test_unsupported_media_is_not_retried_repeatedly(engine):
    engine.store.bind("account", 1000)
    message = Message("1", "chat", "sender", time.time(), "work", {"work_id": "123"})
    engine.store.ingest(message)
    engine.processor.prepare = AsyncMock(side_effect=UnsupportedMedia())
    await engine.prepare_once()
    row = engine.store.list_jobs()[0]
    assert row["state"] == "ready"
    assert row["attempts"] == 1


@pytest.mark.parametrize("kind", ["live", "photo"])
async def test_native_photo_is_forwarded_or_offered_without_static_download(engine, kind):
    data = {"md5": "a" * 32}
    if kind == "live":
        data["live_vid"] = "v01234"
    message = Message("123456", "chat", "sender", time.time(), kind, data)
    engine.store.ingest(message)
    engine.processor.prepare = AsyncMock()
    assert await engine.prepare_once()
    job = engine.store.list_jobs()[0]
    assert job["state"] == "ready"
    part = json.loads(engine.store.parts(job)[0]["payload"])
    expected = {"forward_source": "123456", "md5": ["a" * 32]}
    if kind == "live":
        expected["live_vid"] = "v01234"
        assert "LIVE 实况" in part["text"]
        with engine.store.connect() as db:
            manifest = json.loads(db.execute("SELECT manifest FROM video_choices").fetchone()[0])
        assert manifest["native_live"] == expected
    else:
        assert part == expected
    engine.processor.prepare.assert_not_awaited()


@pytest.mark.parametrize(("frames", "notice"), [(1, True), (3, False)])
@pytest.mark.parametrize("flags", [{"comment_unavailable": True}, {"comment_verified": False}])
async def test_unavailable_comment_never_silently_returns_static_preview(engine, frames, notice, flags):
    message = Message(
        "1",
        "chat",
        "sender",
        time.time(),
        "media",
        {"url": "https://p9.douyinpic.com/a"} | flags,
    )
    engine.processor.prepare = AsyncMock(
        return_value=(
            "cache",
            {
                "files": ["image.gif"],
                "md5": ["digest"],
                "details": [{"frames": frames}],
            },
        )
    )
    engine.store.ingest(message)
    assert await engine.prepare_once()
    job = engine.store.list_jobs()[0]
    payload = json.loads(engine.store.parts(job)[0]["payload"])
    if notice:
        assert "实况视频" in payload["text"]
        assert "无水印" in payload["text"]
        assert "保存" not in payload["text"]
        assert "files" not in payload
    else:
        assert payload["files"] == ["image.gif"]


async def test_first_work_attempt_uses_page_media_and_retry_uses_parser(engine):
    message = Message("1", "chat", "sender", time.time(), "work", {"work_id": "123", "live": True})
    engine.browser.work_media.return_value = [
        {"url": "https://p9.douyinpic.com/a", "video": "https://v3.douyinvod.com/a.mp4", "live": True}
    ]
    manifest = {"files": ["a.jpg", "a.mp4"], "md5": ["a", "b"], "live_photo_count": 1, "live_pairs": [[0, 1]]}
    engine.processor.prepare = AsyncMock(return_value=("cache", manifest))
    engine.store.ingest(message)
    job = engine.store.claim("queued", time.time())
    await engine.prepare_message(job, message)
    assert len(engine.processor.describe.await_args.args[1]) == 2
    engine.browser.work_media.reset_mock()
    await engine.prepare_message(job | {"attempts": 2}, message)
    engine.browser.work_media.assert_not_awaited()
    assert engine.processor.describe.await_args.args[1] is None


async def test_pending_own_upload_without_id_does_not_break_ack_wait():
    rows = [ack(), ack("", flight=1, created=0)]
    page = SimpleNamespace(locator=lambda selector: SimpleNamespace(evaluate_all=AsyncMock(return_value=rows)))
    browser = Browser(page, Settings())
    rows[0]["created"] = 1001
    assert await browser.messages() == [rows[0]]
    rows[1]["from_me"] = False
    with pytest.raises(BrowserUnavailable):
        await browser.messages()


async def test_live_sdk_timings_survive_stale_sdk_receipt(monkeypatch):
    part = {"live_pair": True, "md5": ["digest", "video"], "live_vid": "paired"}
    delivered = ack(type=27, live_vid="paired", live_md5="video", awe_type=2704)
    initial = delivered | {"id": "", "flight": 1, "timings": {"upload_native_image": 2.5}}
    monkeypatch.setattr("qiuyuantu_inbox.browser.send_live_files", AsyncMock(return_value=initial))
    browser = Browser(None, Settings(), "account")
    browser.guard = AsyncMock()
    browser.messages = AsyncMock(side_effect=[[], [delivered]])
    assert await browser.send("chat", part, []) == ["new"]
    assert browser.send_timings["upload_native_image"] == 2.5


@pytest.mark.parametrize(("live_pair", "cost"), [(False, 2), (True, 1)])
async def test_send_rate_charges_outgoing_messages(engine, monkeypatch, tmp_path, live_pair, cost):
    queue_notice(engine)
    job = engine.store.claim("ready", time.time())
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    engine.processor.files = lambda payload: [first, second]
    monkeypatch.setattr("qiuyuantu_inbox.engine.file_md5", lambda path: path.name)
    part = {"number": 0, "payload": json.dumps({"files": ["a", "b"], "md5": ["a", "b"], "live_pair": live_pair})}
    before = time.time()
    await engine._send_part(job, part)
    scheduled = float(engine.store.get_meta("next_send_at"))
    assert (
        before + cost * engine.settings.send_interval <= scheduled <= time.time() + cost * engine.settings.send_interval
    )


async def test_conversation_missing_latest_time_after_restart_is_still_scanned(engine):
    engine.browser.conversations = AsyncMock(return_value=[{"id": "chat", "last_at": 0, "unread": 0}])
    engine.browser.collect = AsyncMock(return_value=([event()], True))
    await engine.collect_once()
    engine.browser.collect.assert_awaited_once()
    assert engine.store.status()["counts"] == {"queued": 1}


async def test_partial_conversation_round_preserves_catchup_flag(engine):
    engine.scope = set()
    engine.browser.conversations = AsyncMock(
        return_value=[{"id": str(i), "last_at": 0, "unread": 0} for i in range(11)]
    )
    engine.browser.collect = AsyncMock(return_value=([], True))
    assert not await engine.collect_once()
    assert engine.browser.collect.await_count == 10
    assert engine.store.get_meta("catchup_required") == "1"


async def test_scan_releases_ui_between_conversations_for_waiting_sender(engine):
    engine.browser.conversations = AsyncMock(
        return_value=[{"id": name, "last_at": 0, "unread": 0} for name in ("first", "second")]
    )
    first_entered, release_first = asyncio.Event(), asyncio.Event()
    order = []

    async def collect(cid, since):
        order.append(cid)
        if cid == "first":
            first_entered.set()
            await release_first.wait()
        return [], True

    async def foreground():
        async with engine.ui:
            order.append("send")

    engine.browser.collect = AsyncMock(side_effect=collect)
    scanner = asyncio.create_task(engine.collect_once())
    sender = None
    try:
        await asyncio.wait_for(first_entered.wait(), 1)
        sender = asyncio.create_task(foreground())
        await asyncio.sleep(0)
        release_first.set()
        assert await asyncio.wait_for(scanner, 1)
        await asyncio.wait_for(sender, 1)
        assert order == ["first", "send", "second"]
    finally:
        tasks = [scanner, *([sender] if sender else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_pause_mid_scan_preserves_remaining_cursor_and_catchup(engine):
    engine.browser.conversations = AsyncMock(
        return_value=[{"id": name, "last_at": 0, "unread": 0} for name in ("first", "second")]
    )

    async def collect(cid, since):
        engine.store.set_meta("paused", "operator")
        return [], True

    engine.browser.collect = AsyncMock(side_effect=collect)
    assert not await engine.collect_once()
    assert engine.browser.collect.await_count == 1
    assert engine.store.cursor("first") > 1000 and engine.store.cursor("second") == 1000
    assert engine.store.get_meta("catchup_required") == "1"


async def test_scan_does_not_capture_or_advance_cursor_for_wrong_conversation(engine):
    engine.browser.conversations = AsyncMock(return_value=[{"id": "chat", "last_at": 0, "unread": 0}])
    engine.browser.collect = AsyncMock(return_value=([event(conversation="other")], True))
    with pytest.raises(BrowserUnavailable, match="conversation_changed_during_read"):
        await engine.collect_once()
    assert engine.store.inbound() == [] and engine.store.cursor("chat") == 1000


async def test_capture_filters_new_conversation_history_without_resetting_existing_chat(engine):
    engine.store.set_meta("start_at:new-chat", "2000")
    await engine.capture_rows([event(conversation="new-chat"), event(id="existing")])
    assert engine.store.status()["counts"] == {"queued": 1}
    assert engine.store.list_jobs()[0]["id"] == message_from_snapshot(event(id="existing")).key


@pytest.mark.parametrize("confirmed_empty", [False, True])
async def test_empty_chat_requires_client_confirmation_instead_of_pausing_account(confirmed_empty):
    browser = Browser(MagicMock(), Settings(), "account")
    browser.open = AsyncMock()
    browser.messages = AsyncMock(return_value=[])
    browser.empty_conversation = AsyncMock(return_value=confirmed_empty)
    browser.page.locator.return_value.evaluate = AsyncMock()
    assert await browser.collect("chat", 1000) == ([], confirmed_empty)
    browser.empty_conversation.assert_awaited_once_with("chat")


async def test_hidden_message_list_is_opened_using_icon_button():
    page = MagicMock()
    items = page.locator.return_value.filter.return_value
    items.count = AsyncMock(return_value=0)
    items.first.wait_for = AsyncMock()
    scroll = page.locator.return_value.filter.return_value
    scroll.evaluate = AsyncMock(return_value=True)
    page.get_by_text.return_value.is_visible = AsyncMock(return_value=False)
    icon = page.locator.return_value
    icon.click = AsyncMock()
    browser = Browser(page, Settings(), "account")
    browser._list = AsyncMock(return_value=[{"id": "chat", "account": "account"}])
    browser.back = AsyncMock()
    assert await browser.conversations() == [{"id": "chat", "account": "account"}]
    icon.click.assert_awaited_once()
    page.locator.assert_any_call('[data-e2e="im-entry"] [data-e2e="something-button"]')


@pytest.mark.parametrize("mode", ["video", "gif", "both"])
async def test_video_waits_for_choice_then_sends_only_selected_outputs(engine, mode):
    source = {"files": ["source.mp4"], "md5": ["video"], "details": [{"format": "MP4"}]}
    converted = {"files": ["clip.gif"], "md5": ["gif"], "details": [{"frames": 72}]}
    engine.processor.prepare = AsyncMock(return_value=("source", source))
    engine.processor.gif_variant = AsyncMock(return_value=("converted", converted))
    engine.processor.files = lambda payload: []
    engine.settings = Settings(send_interval=0.001)
    message = Message("video", "chat", "sender", time.time(), "work", {"work_id": "123"})
    engine.store.ingest(message)
    assert await engine.prepare_once()
    engine.processor.gif_variant.assert_not_awaited()
    assert await engine.send_once()
    assert engine.store.status()["counts"] == {"waiting": 1}
    assert "1 原视频" in engine.browser.send.await_args.args[1]["text"]
    command = Message("choice", "chat", "sender", time.time(), "selection", {"mode": mode, "code": ""})
    assert engine.store.select_video(command, time.time()) == "selected"
    assert await engine.prepare_once()
    await drain_sends(engine)
    assert engine.store.status()["counts"] == {"sent": 1}
    files = [call.args[1]["files"] for call in engine.browser.send.await_args_list[1:]]
    assert files == {"video": [["source.mp4"]], "gif": [["clip.gif"]], "both": [["source.mp4"], ["clip.gif"]]}[mode]
    assert engine.processor.prepare.await_count == 1
    assert engine.processor.gif_variant.await_count == (mode != "video")


@pytest.mark.parametrize("mode", ["video", "gif", "both"])
async def test_live_gallery_choice_keeps_pairs_and_converts_only_live_motion(engine, mode):
    source = {
        "files": ["1.jpg", "1.mp4", "still.jpg", "2.jpg", "2.mp4"],
        "md5": ["image1", "video1", "still", "image2", "video2"],
        "details": [{"frames": 1}] * 5,
        "live_photo_count": 2,
        "live_pairs": [[0, 1], [3, 4]],
    }
    converted = {"files": ["1.gif", "2.gif"], "md5": ["gif1", "gif2"], "details": [{"frames": 24}] * 2}
    engine.processor.prepare = AsyncMock(return_value=("source", source))
    engine.processor.gif_variant = AsyncMock(return_value=("converted", converted))
    engine.processor.files = lambda payload: []
    engine.settings = Settings(send_interval=0.001)
    message = Message("live-gallery", "chat", "sender", time.time(), "work", {"work_id": "123", "live": True})
    engine.store.ingest(message)
    assert await engine.prepare_once()
    assert await engine.send_once()
    prompt = engine.browser.send.await_args.args[1]["text"]
    assert "LIVE 实况" in prompt and "全部 2 张实况" in prompt
    engine.processor.gif_variant.assert_not_awaited()
    command = Message("choice", "chat", "sender", time.time(), "selection", {"mode": mode, "code": ""})
    assert engine.store.select_video(command, time.time()) == "selected"
    assert await engine.prepare_once()
    await drain_sends(engine)
    payloads = [call.args[1] for call in engine.browser.send.await_args_list[1:]]
    live_parts = [part for part in payloads if part.get("live_pair")]
    assert len(live_parts) == (0 if mode == "gif" else 2)
    assert [part["md5"] for part in live_parts] == (
        [] if mode == "gif" else [["image1", "video1"], ["image2", "video2"]]
    )
    assert any("1.gif" in part["files"] for part in payloads) == (mode != "video")
    assert engine.store.status()["counts"] == {"sent": 1}


@pytest.mark.parametrize("mode", ["video", "gif", "both"])
async def test_native_live_choice_downloads_motion_only_for_gif_and_keeps_forward_source(engine, mode):
    message = Message(
        "123456", "chat", "sender", time.time(), "live", {"md5": "image", "live_vid": "pair", "live_md5": "video"}
    )
    motion = {"files": ["motion.mp4"], "md5": ["plaintext-video"], "details": [{"format": "MP4"}]}
    converted = {"files": ["clip.gif"], "md5": ["gif"], "details": [{"frames": 24}]}
    engine.processor.native_motion = AsyncMock(return_value=("motion", motion))
    engine.browser.native_motion = AsyncMock()
    engine.processor.gif_variant = AsyncMock(return_value=("converted", converted))
    engine.processor.files = lambda payload: []
    engine.settings = Settings(send_interval=0.001)
    engine.store.ingest(message)
    assert await engine.prepare_once()
    engine.processor.native_motion.assert_not_awaited()
    assert await engine.send_once()
    command = Message("choice", "chat", "sender", time.time(), "selection", {"mode": mode, "code": ""})
    assert engine.store.select_video(command, time.time()) == "selected"
    assert await engine.prepare_once()
    await drain_sends(engine)
    payloads = [call.args[1] for call in engine.browser.send.await_args_list[1:]]
    native = {"forward_source": "123456", "md5": ["image"], "live_vid": "pair", "native_video_md5": "video"}
    assert (native in payloads) == (mode != "gif")
    assert any(part.get("files") == ["clip.gif"] for part in payloads) == (mode != "video")
    assert engine.processor.native_motion.await_count == (mode != "video")
    assert engine.store.status()["counts"] == {"sent": 1}
