"""选择等待期间的原格式预热：只预下载和预传缓存，正式任务优先，不改变选择结果。"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox import media
from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.store import Store
from qiuyuantu_inbox.warmup import ChoiceWarmup

VIDEO = b"\0\0\0\x18ftypmp42" + b"\0" * 64


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "queue", Settings())
    result.bind("account", 1000)
    return result


def offer(store, manifest, number="1"):
    now = time.time()
    message = Message(number, "chat", "sender", now, "media", {"url": "https://v3.douyinvod.com/" + number})
    assert store.ingest(message, now) == "queued"
    job = store.claim("queued", now)
    store.ready(job, "", [{"offer_video": manifest}], now)
    sending = store.claim("ready", now)
    store.start_part(sending, 0, now)
    store.finish_part(sending, 0, ["prompt-" + number])
    store.finish(sending, "sent", now)
    return message


def metadata(number="1"):
    return {
        "metadata_only": True,
        "resolved_at": time.time(),
        "assets": [{"url": "https://v3.douyinvod.com/" + number, "video": True, "live": False, "title": ""}],
        "selectable": True,
        "live_photo_count": 0,
        "title": "测试视频",
    }


def warmup(store, monkeypatch, scope=frozenset()):
    async def download(url, path, **kwargs):
        path.write_bytes(VIDEO)

    fetch = AsyncMock(side_effect=download)
    monkeypatch.setattr(media, "fetch_bytes", fetch)
    processor = media.Processor(store, store.settings)
    engine = SimpleNamespace(
        processor=processor, scope=set(scope), stopped=asyncio.Event(), paused=AsyncMock(return_value=False)
    )
    bridge = SimpleNamespace(warm=AsyncMock(return_value={"handle": "h", "upload_seconds": 0.5}))

    async def files(part):
        return processor.files(part)

    pipeline = SimpleNamespace(engine=engine, store=store, settings=store.settings, bridge=bridge, files=files)
    return ChoiceWarmup(pipeline), fetch, bridge


def choice(store, message):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM video_choices WHERE job=?", (message.key,)).fetchone())


async def test_waiting_video_is_downloaded_and_preuploaded_once(store, monkeypatch):
    message = offer(store, metadata())
    warm, fetch, bridge = warmup(store, monkeypatch)
    assert await warm.once()
    source = json.loads(choice(store, message)["manifest"])
    assert not source.get("metadata_only") and source["files"][0].endswith(".mp4")
    part = bridge.warm.await_args.args[0]
    assert part["video"] and part["md5"] == source["md5"]
    with store.connect() as db:
        phases = dict(db.execute("SELECT phase,seconds FROM timings WHERE job=?", (message.key,)))
    assert {"choice_warmup", "choice_warmup_download", "choice_warmup_upload"} <= phases.keys()
    assert phases["choice_warmup_stream"] == 0.5
    # 已尝试过的待选不重复预热，正式准备直接复用缓存。
    assert not await warm.once()
    key, cached = await warm.engine.processor.prepare(message, [media.Asset(message.data["url"], video=True)])
    assert cached["files"] == source["files"] and fetch.await_count == 1


async def test_warmup_yields_to_pending_real_work(store, monkeypatch):
    offer(store, metadata())
    queued = Message("2", "chat-2", "other", time.time(), "media", {"url": "https://p9.douyinpic.com/2"})
    assert store.ingest(queued, time.time()) == "queued"
    warm, fetch, bridge = warmup(store, monkeypatch)
    assert not await warm.idle()
    store.claim("queued", time.time())
    assert not await warm.idle()  # 正式准备进行中同样让路


async def test_native_album_live_and_other_scope_are_skipped(store, monkeypatch):
    offer(store, {"native_live": {"forward_source": "1"}, "files": [], "md5": [], "details": []})
    warm, fetch, bridge = warmup(store, monkeypatch)
    assert await warm.once()
    assert fetch.await_count == 0 and bridge.warm.await_count == 0
    offer(store, metadata("2"), "2")
    warm, fetch, bridge = warmup(store, monkeypatch, scope={"other-chat"})
    assert not await warm.once()


async def test_formal_source_written_meanwhile_is_not_replaced(store, monkeypatch):
    message = offer(store, metadata())
    previous = choice(store, message)["manifest"]
    replaced = {"files": ["artifacts/formal/video.mp4"], "md5": ["formal"], "details": [{}]}
    with store.connect() as db:
        db.execute("UPDATE video_choices SET manifest=? WHERE job=?", (json.dumps(replaced), message.key))
    assert not store.warm_choice_source(message.key, previous, "warm", {"files": []})
    assert json.loads(choice(store, message)["manifest"]) == replaced


async def test_failed_preupload_keeps_downloaded_source(store, monkeypatch):
    message = offer(store, metadata())
    warm, fetch, bridge = warmup(store, monkeypatch)
    bridge.warm.side_effect = RuntimeError("outbound_upload_expired")
    assert await warm.once()
    assert not json.loads(choice(store, message)["manifest"]).get("metadata_only")
    with store.connect() as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (message.key,)).fetchone()[0] == "waiting"
