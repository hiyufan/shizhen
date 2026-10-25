"""视频选择后的 GIF 限制、缓存合并与取消清理。"""

import asyncio
import hashlib
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from qiuyuantu_inbox import media, video_gif
from qiuyuantu_inbox.models import Message, Settings
from qiuyuantu_inbox.store import Store


def animated(path):
    frames = [Image.new("RGB", (24, 16), color) for color in ("red", "blue")]
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return video_gif.gif_details(path)


async def test_oversized_gif_retries_with_smaller_dimensions_and_frame_rate(monkeypatch, tmp_path):
    target = tmp_path / "clip.gif"

    async def encode(src, dst, **kwargs):
        if kwargs["width"] == 480:
            target.write_bytes(b"x" * 2000)
        else:
            animated(target)

    encoder = AsyncMock(side_effect=encode)
    monkeypatch.setattr(video_gif.ffmpeg, "make_gif", encoder)
    result = await video_gif.encode_gif(tmp_path / "source.mp4", target, Settings(file_bytes=1000))
    assert result["frames"] == 2
    assert [call.kwargs["width"] for call in encoder.await_args_list] == [480, 320]
    assert [call.kwargs["fps"] for call in encoder.await_args_list] == [12, 8]
    assert all(call.kwargs["duration"] == 6 for call in encoder.await_args_list)


async def test_still_oversized_gif_is_rejected(monkeypatch, tmp_path):
    target = tmp_path / "clip.gif"

    async def encode(*args, **kwargs):
        target.write_bytes(b"x" * 2000)

    monkeypatch.setattr(video_gif.ffmpeg, "make_gif", encode)
    with pytest.raises(ValueError, match="大小限制"):
        await video_gif.encode_gif(tmp_path / "source.mp4", target, Settings(file_bytes=1000))


async def test_gif_requests_share_one_conversion(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    source = processor.root / "original"
    source.mkdir()
    (source / "video.mp4").write_bytes(b"source")
    manifest = {"files": ["artifacts/original/video.mp4"], "md5": ["source"]}

    async def convert(src, dst, settings):
        await asyncio.sleep(0.02)
        return animated(dst)

    encode = AsyncMock(side_effect=convert)
    monkeypatch.setattr(media, "encode_gif", encode)
    first, second = await asyncio.gather(processor.gif_variant(manifest), processor.gif_variant(manifest))
    assert first == second
    assert encode.await_count == 1
    assert processor.reserved_bytes == 0


async def test_cancelled_gif_releases_temporary_files_and_reservation(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    started, stopped = asyncio.Event(), asyncio.Event()
    (processor.root / "source.mp4").write_bytes(b"source")

    async def convert(src, dst, settings):
        dst.write_bytes(b"partial")
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(media, "encode_gif", convert)
    task = asyncio.create_task(processor.gif_variant({"files": ["artifacts/source.mp4"], "md5": ["source"]}))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert processor.reserved_bytes == 0
    assert [path.name for path in processor.root.iterdir()] == ["source.mp4"]


async def test_real_portrait_video_becomes_animated_gif_with_bounded_duration_and_edges(tmp_path):
    encoder = shutil.which("ffmpeg")
    if not encoder:
        pytest.skip("需要 ffmpeg")
    source, target = tmp_path / "portrait.mp4", tmp_path / "clip.gif"
    subprocess.run(
        [encoder, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=180x640:rate=12", "-t", "2", str(source)],
        check=True,
        capture_output=True,
        timeout=20,
    )
    details = await video_gif.encode_gif(source, target, Settings(gif_seconds=1))
    assert details["frames"] > 1
    assert max(details["width"], details["height"]) <= 480
    with Image.open(target) as image:
        duration = 0
        for frame in range(image.n_frames):
            image.seek(frame)
            duration += image.info["duration"]
    assert 900 <= duration <= 1100


async def test_live_gallery_gifs_follow_paired_videos_and_exclude_stills(monkeypatch, tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    source = processor.root / "original"
    source.mkdir()
    names = ["1.jpg", "1.mp4", "still.jpg", "2.jpg", "2.mp4"]
    for name in names:
        (source / name).write_bytes(name.encode())
    manifest = {
        "files": [f"artifacts/original/{name}" for name in names],
        "md5": names,
        "live_photo_count": 2,
        "live_pairs": [[0, 1], [3, 4]],
    }

    async def convert(src, dst, settings):
        return animated(dst)

    encode = AsyncMock(side_effect=convert)
    monkeypatch.setattr(media, "encode_gif", encode)
    key, result = await processor.gif_variant(manifest)
    assert [call.args[0].name for call in encode.await_args_list] == ["1.mp4", "2.mp4"]
    assert len(processor.files(result)) == 2
    assert result["details"][0]["frames"] == 2
    assert video_gif.gif_key(manifest | {"md5": ["new-still", *names[1:]]}, store.settings) == key
    assert video_gif.gif_key(manifest | {"md5": [*names[:4], "new-video"]}, store.settings) != key


async def test_native_motion_records_plaintext_digest_separately_and_reuses_verified_source(tmp_path):
    store = Store(tmp_path / "queue", Settings())
    processor = media.Processor(store, store.settings)
    data = b"\0\0\0\x18ftypisom"
    message = Message("123456", "chat", "sender", 1001, "live", {"live_md5": "encrypted-digest"})
    download = AsyncMock(return_value=data)
    first = await processor.native_motion(message, download)
    second = await processor.native_motion(message, download)
    assert first == second
    assert download.await_count == 1
    manifest = first[1]
    assert manifest["source_md5"] == "encrypted-digest"
    assert manifest["md5"] == [hashlib.md5(data, usedforsecurity=False).hexdigest()]
    assert processor.reserved_bytes == 0
