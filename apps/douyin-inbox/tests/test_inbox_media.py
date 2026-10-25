"""动画不降成单帧，缓存不越界，编码取消后再清理目录。"""

import asyncio
import threading
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from qiuyuantu.parser.base import ImgInfo, VideoInfo
from qiuyuantu_inbox import media
from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.store import Store


def test_gif_is_returned_without_reencoding(tmp_path):
    source = tmp_path / "input.source"
    frames = [Image.new("RGB", (12, 8), color) for color in ("red", "blue", "green")]
    frames[0].save(source, format="GIF", save_all=True, append_images=frames[1:], duration=[40, 70, 90], loop=0)
    original = source.read_bytes()
    target, details = media.image_file(source)
    assert target.suffix == ".gif"
    assert target.read_bytes() == original
    assert details["frames"] == 3


def test_webp_source_is_retained_when_upload_uses_png(tmp_path):
    source = tmp_path / "input.source"
    Image.new("RGB", (12, 8), "red").save(source, format="WEBP")
    original = source.read_bytes()
    target, details = media.image_file(source)
    assert target.suffix == ".png"
    assert source.with_suffix(".webp").read_bytes() == original
    assert details["format"] == "WEBP"


async def test_simultaneous_same_media_downloads_only_once(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)

    async def download(url, path, **kwargs):
        await asyncio.sleep(0.02)
        Image.new("RGB", (12, 8), "blue").save(path, format="JPEG")

    fetch = AsyncMock(side_effect=download)
    monkeypatch.setattr(media, "fetch_bytes", fetch)
    message = Message("1", "chat", "sender", 1001, "media", {"url": "https://p11.douyinpic.com/obj/a"})
    first, second = await asyncio.gather(processor.prepare(message), processor.prepare(message))
    assert first == second
    assert fetch.await_count == 1


def test_cached_paths_cannot_escape_artifact_directory(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    file = tmp_path / "private"
    file.write_bytes(b"private")
    with pytest.raises(media.UnsupportedMedia):
        processor.files({"files": [str(file)]})


async def test_cancelled_image_thread_finishes_before_cleanup():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def encode():
        started.set()
        release.wait(timeout=2)
        finished.set()

    task = asyncio.create_task(media.blocking(encode))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_connection_failure_retries_immediately_with_short_timeout(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    fetch = AsyncMock(side_effect=[httpx.ConnectTimeout("offline"), None])
    monkeypatch.setattr(media, "fetch_bytes", fetch)
    await processor._fetch("https://p11.douyinpic.com/obj/a", tmp_path / "source", 20)
    assert fetch.await_count == 2
    assert all(call.kwargs == {"limit": 20, "connect_timeout": 5} for call in fetch.await_args_list)


async def test_midstream_failure_does_not_repeat_large_download(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    fetch = AsyncMock(side_effect=httpx.ReadTimeout("interrupted"))
    monkeypatch.setattr(media, "fetch_bytes", fetch)
    with pytest.raises(httpx.ReadTimeout):
        await processor._fetch("https://p11.douyinpic.com/obj/a", tmp_path / "source", 20)
    assert fetch.await_count == 1


async def test_post_live_photo_keeps_still_and_paired_video(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    info = VideoInfo(video_url="", cover_url="", images=[ImgInfo(url="image", live_photo_url="video")])
    monkeypatch.setattr(media, "parse_video_share_url", AsyncMock(return_value=info))
    assets = await processor._urls(Message("1", "chat", "sender", 1001, "work", {"work_id": "123", "live": True}))
    assert assets == [media.Asset("image"), media.Asset("video", video=True, live=True)]


async def test_missing_post_live_video_cannot_silently_become_static(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    info = VideoInfo(video_url="", cover_url="", images=[ImgInfo(url="image")])
    monkeypatch.setattr(media, "parse_video_share_url", AsyncMock(return_value=info))
    with pytest.raises(RuntimeError, match="实况视频"):
        await processor._urls(Message("1", "chat", "sender", 1001, "work", {"work_id": "123", "live": True}))


def test_live_photos_are_paired_in_order_and_ordinary_images_stay_separate():
    manifest = {
        "files": ["1.jpg", "1.mp4", "2.jpg", "3.png", "3.mp4"],
        "md5": ["a", "b", "c", "d", "e"],
        "live_photo_count": 2,
        "live_pairs": [[0, 1], [3, 4]],
    }
    parts = media.delivery_parts(manifest)
    assert [p["files"] for p in parts] == [["1.jpg", "1.mp4"], ["2.jpg"], ["3.png", "3.mp4"]]
    assert parts[0]["live_pair"] and parts[2]["live_pair"]
    assert parts[0]["md5"] == ["a", "b"]
    assert parts[0]["client_id"] != parts[2]["client_id"]
    assert parts[0]["live_vid"] != parts[2]["live_vid"]
    assert not parts[1]["video"]


@pytest.mark.parametrize("pairs", [[], [[0, 2]], [[-1, 0]], [[0, 3]], [[1, 2]], [[0, 1], [0, 1]]])
def test_invalid_pairing_cannot_fall_back_to_still_photos(pairs):
    manifest = {
        "files": ["1.jpg", "1.mp4", "2.jpg"],
        "md5": ["a", "b", "c"],
        "live_photo_count": max(1, len(pairs)),
        "live_pairs": pairs,
    }
    with pytest.raises(media.UnsupportedMedia):
        media.delivery_parts(manifest)


def test_regular_video_uploads_remain_separate_from_images():
    parts = media.delivery_parts({"files": ["1.jpg", "2.mp4", "3.jpg"], "md5": ["a", "b", "c"]})
    assert [part["files"] for part in parts] == [["1.jpg"], ["2.mp4"], ["3.jpg"]]
    assert parts[1]["video"]
