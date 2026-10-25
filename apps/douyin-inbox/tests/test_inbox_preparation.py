"""先识别后下载；GIF 只取动态资源，依赖请求释放 worker。"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.engine import Engine
from qiuyuantu_inbox.media import Asset
from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.store import Store


@pytest.fixture
def engine(tmp_path):
    store = Store(tmp_path / "queue", Settings(send_interval=0.001))
    store.bind("account", 1000)
    browser = SimpleNamespace(
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
