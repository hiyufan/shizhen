"""先识别后下载；GIF 只取动态资源，依赖请求释放 worker。"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.engine import Engine
from qiuyuantu_inbox.media import Asset
from qiuyuantu_inbox.models import Message, Settings, content_key
from qiuyuantu_inbox.store import Store


@pytest.fixture
def engine(tmp_path):
    store = Store(tmp_path / "queue", Settings(send_interval=0.001))
    store.bind("account", 1000)
    browser = SimpleNamespace(
        cached_work_media=AsyncMock(return_value=None),
        work_media=AsyncMock(return_value=None),
        open=AsyncMock(),
        guard=AsyncMock(),
        send=AsyncMock(return_value=["server-receipt"]),
    )
    return Engine(store, browser, set(), True)


@pytest.mark.parametrize("mode", ["video", "gif", "both"])
async def test_choice_prompt_does_not_download_and_gif_downloads_only_motion(engine, mode):
    message = Message("1", "chat", "sender", time.time(), "work", {"work_id": "123", "live": True})
    assets = [
        Asset("https://p9.douyinpic.com/1"),
        Asset("https://v3.douyinvod.com/1", video=True, live=True),
        Asset("https://p9.douyinpic.com/2"),
        Asset("https://v3.douyinvod.com/2", video=True, live=True),
    ]
    engine.processor._urls = AsyncMock(return_value=assets)
    motion = {"files": ["a.mp4", "b.mp4"], "md5": ["a", "b"], "details": [{"format": "MP4"}] * 2}
    full = {
        "files": ["a.jpg", "a.mp4", "b.jpg", "b.mp4"],
        "md5": ["i1", "a", "i2", "b"],
        "details": [{"format": "MP4"}] * 4,
        "live_photo_count": 2,
        "live_pairs": [[0, 1], [2, 3]],
    }
    engine.processor.prepare = AsyncMock(return_value=("source", motion if mode == "gif" else full))
    engine.processor.gif_variant = AsyncMock(return_value=("gif", {"files": ["a.gif", "b.gif"], "md5": ["g1", "g2"]}))
    engine.store.ingest(message)
    assert await engine.prepare_once()
    engine.processor.prepare.assert_not_awaited()
    assert await engine.send_once()
    assert engine.store.status()["counts"] == {"waiting": 1}
    command = Message("2", "chat", "sender", time.time(), "selection", {"mode": mode, "code": ""})
    assert engine.store.select_video(command, time.time()) == "selected"
    assert await engine.prepare_once()
    selected_assets = engine.processor.prepare.await_args.args[1]
    if mode == "gif":
        assert len(selected_assets) == 2 and all(asset.video and not asset.live for asset in selected_assets)
        assert engine.processor.prepare.await_args.kwargs["variant"] == "motion"
    else:
        assert selected_assets == assets
    assert engine.processor.gif_variant.await_count == (mode != "video")


async def test_shared_download_waiter_releases_worker_for_another_user(engine):
    first_started, release = asyncio.Event(), asyncio.Event()

    async def prepare(message, assets):
        if message.sender == "a":
            first_started.set()
            await release.wait()
        return "cache", {"files": ["image.gif"], "md5": ["digest"], "details": [{"frames": 2}]}

    engine.processor.prepare = AsyncMock(side_effect=prepare)
    for number, sender, url in [("1", "a", "same"), ("2", "b", "same"), ("3", "c", "other")]:
        engine.store.ingest(
            Message(number, sender, sender, time.time(), "media", {"url": "https://p9.douyinpic.com/" + url})
        )
    owner = asyncio.create_task(engine.prepare_once())
    await asyncio.wait_for(first_started.wait(), 1)
    assert await engine.prepare_once()
    assert engine.store.status()["counts"].get("blocked") == 1
    assert await asyncio.wait_for(engine.prepare_once(), 1)
    assert engine.processor.prepare.await_count == 2
    release.set()
    await owner
    assert engine.store.status()["counts"] == {"ready": 2, "queued": 1}


def work_job(engine):
    message = Message("source", "chat", "sender", time.time(), "work", {"work_id": "123", "live": True})
    engine.store.ingest(message)
    return message, engine.store.claim("queued", time.time())


def live_snapshot():
    return [{"url": "https://p9.douyinpic.com/a", "video": "https://v3.douyinvod.com/a.mp4", "live": True}]


async def test_client_cache_identifies_live_without_waiting_for_busy_ui_or_downloading(engine):
    message, job = work_job(engine)
    engine.browser.cached_work_media.return_value = live_snapshot()
    engine.processor._urls = AsyncMock(side_effect=AssertionError("不得重新解析"))
    engine.processor.prepare = AsyncMock(side_effect=AssertionError("选择前不得下载"))
    await engine.ui.acquire()
    try:
        _, parts = await asyncio.wait_for(engine.prepare_message(job, message), 1)
    finally:
        engine.ui.release()
    source = parts[0]["offer_video"]
    assert source["live_photo_count"] == 1 and source["metadata_only"]
    assert [asset["video"] for asset in source["assets"]] == [False, True]
    engine.browser.work_media.assert_not_awaited()
    engine.processor._urls.assert_not_awaited()
    engine.processor.prepare.assert_not_awaited()
    with engine.store.connect() as db:
        phases = dict(db.execute("SELECT phase,seconds FROM timings WHERE job=?", (job["id"],)))
    assert {"source_local_cache", "source_client_cache", "source_metadata"} <= phases.keys()
    assert "source_ui_wait" not in phases and "source_parser" not in phases


async def test_local_artifact_is_used_before_browser_or_parser_even_with_busy_ui(engine):
    message, job = work_job(engine)
    key = content_key(message)
    directory = engine.processor.root / key
    directory.mkdir()
    file = directory / "source.mp4"
    file.write_bytes(b"video")
    manifest = {"files": [str(file.relative_to(engine.store.root))], "md5": ["digest"], "details": [{}]}
    engine.store.cache(key, manifest, 5, time.time())
    engine.processor._urls = AsyncMock(side_effect=AssertionError("不得重新解析"))
    await engine.ui.acquire()
    try:
        _, parts = await asyncio.wait_for(engine.prepare_message(job, message), 1)
    finally:
        engine.ui.release()
    assert parts == [{"offer_video": manifest}]
    engine.browser.cached_work_media.assert_not_awaited()
    engine.browser.work_media.assert_not_awaited()
    engine.processor._urls.assert_not_awaited()


async def test_passive_cache_miss_falls_back_under_ui_lock(engine):
    message, job = work_job(engine)

    async def read(_message):
        assert engine.ui.locked()
        return live_snapshot()

    engine.browser.work_media.side_effect = read
    engine.processor._urls = AsyncMock(side_effect=AssertionError("已取得客户端详情"))
    _, parts = await engine.prepare_message(job, message)
    assert parts[0]["offer_video"]["live_photo_count"] == 1
    assert engine.browser.cached_work_media.await_count == 2
    engine.browser.cached_work_media.assert_awaited_with(message, resolve=True)
    engine.browser.work_media.assert_awaited_once()
    with engine.store.connect() as db:
        phases = dict(db.execute("SELECT phase,seconds FROM timings WHERE job=?", (job["id"],)))
    assert "source_ui_wait" in phases and "source_ui_read" in phases


async def test_missing_client_cache_loads_details_without_waiting_for_ui_or_anonymous_parser(engine):
    message, job = work_job(engine)

    async def read(_message, *, resolve=False):
        return live_snapshot() if resolve else None

    engine.browser.cached_work_media.side_effect = read
    engine.processor._urls = AsyncMock(side_effect=AssertionError("不应重复打开匿名解析页"))
    async with engine.ui:
        _, parts = await asyncio.wait_for(engine.prepare_message(job, message), 1)
    assert parts[0]["offer_video"]["live_photo_count"] == 1
    engine.browser.work_media.assert_not_awaited()
    with engine.store.connect() as db:
        phases = dict(db.execute("SELECT phase,seconds FROM timings WHERE job=?", (job["id"],)))
    assert "source_client_fetch" in phases and "source_ui_wait" not in phases


async def test_missing_local_file_and_invalid_page_url_still_use_safe_parser_fallback(engine):
    message, job = work_job(engine)
    key = content_key(message)
    engine.store.cache(key, {"files": [f"artifacts/{key}/deleted.mp4"]}, 1, time.time())
    engine.browser.cached_work_media.return_value = [{"url": "http://127.0.0.1/private"}]
    engine.processor._urls = AsyncMock(return_value=[Asset("https://v3.douyinvod.com/a.mp4", video=True)])
    _, parts = await engine.prepare_message(job, message)
    assert parts[0]["offer_video"]["selectable"]
    engine.processor._urls.assert_awaited_once()
    engine.browser.work_media.assert_awaited_once()
