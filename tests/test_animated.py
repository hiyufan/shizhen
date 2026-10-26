"""动态音乐封面的解码、准备、资源边界与取消；平台响应仍用离线文件。"""

import asyncio
import threading

import pytest
from PIL import Image

from qiuyuantu.convert import animated, ffmpeg, jobs, store, tasks


def animation(path, *, durations=(40, 200, 80), size=(16, 16)):
    frames = [Image.new("RGB", size, (index * 50, 20, 100)) for index in range(len(durations))]
    frames[0].save(
        path, format="WEBP", save_all=True, append_images=frames[1:], duration=list(durations), lossless=True
    )


def test_mislabeled_webp_keeps_all_frames_and_variable_timing(tmp_path):
    source = tmp_path / "cover.jpg"
    animation(source)
    directory = tmp_path / "frames"
    directory.mkdir()
    assert animated._write_frames(source, directory, threading.Event()) == pytest.approx(0.32)
    assert len(list(directory.glob("*.png"))) == 3
    manifest = (directory / "frames.txt").read_text()
    assert "duration 0.040000" in manifest and "duration 0.200000" in manifest
    assert manifest.endswith("file 'frame0002.png'\n")


@pytest.mark.parametrize("kind", ["static", "invalid", "pixels", "duration"])
def test_unusable_images_are_rejected_with_a_specific_reason(tmp_path, kind):
    source = tmp_path / "cover.jpg"
    if kind == "static":
        Image.new("RGB", (16, 16)).save(source)
    elif kind == "invalid":
        source.write_text("not an image")
    else:
        animation(source, durations=(16000, 16000) if kind == "duration" else (40, 40))
    with pytest.MonkeyPatch.context() as patch:
        if kind == "pixels":
            original = Image.open

            def oversized(path):
                image = original(path)
                image._size = (10000, 10000)
                return image

            patch.setattr(Image, "open", oversized)
        with pytest.raises(
            ValueError,
            match={"static": "静态图片", "invalid": "不是可用", "pixels": "像素限制", "duration": "30 秒"}[kind],
        ):
            animated._write_frames(source, tmp_path, threading.Event())


async def test_cancel_waits_for_decoder_before_removing_temporary_frames(tmp_path, monkeypatch):
    started, finished = threading.Event(), threading.Event()

    def decode(_source, directory, stopped):
        (directory / "frame.png").write_bytes(b"frame")
        started.set()
        stopped.wait(5)
        assert directory.exists()
        finished.set()
        return 0

    monkeypatch.setattr(animated, "_write_frames", decode)
    task = asyncio.create_task(animated.prepare(tmp_path / "cover", tmp_path / "video.mp4"))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set() and not list(tmp_path.glob("animation-*"))


async def test_encode_failure_removes_incomplete_video_and_frames(tmp_path, monkeypatch):
    source, destination = tmp_path / "cover.jpg", tmp_path / "video.mp4"
    animation(source)

    async def failed(args, **kwargs):
        assert args[args.index("-safe") + 1] == "1"
        assert kwargs["total"] == pytest.approx(0.32)
        destination.write_bytes(b"partial")
        raise RuntimeError("encode failed")

    monkeypatch.setattr(ffmpeg, "run", failed)
    with pytest.raises(RuntimeError, match="encode failed"):
        await animated.prepare(source, destination)
    assert not destination.exists() and not list(tmp_path.glob("animation-*"))


async def test_prepare_decodes_signed_cover_without_ytdlp_and_caches_video(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks.config, "SOURCES_DIR", tmp_path)
    prepared = []

    async def download(url, path, headers, on_bytes):
        assert url == "https://p1.music.126.net/cover.jpg"
        animation(path)

    async def encode(source, destination):
        with Image.open(source) as image:
            assert image.format == "WEBP" and image.n_frames == 3
        prepared.append(destination)
        destination.write_bytes(b"video")

    async def probe(path):
        assert path.read_bytes() == b"video"
        return ffmpeg.ProbeInfo(duration=0.32, width=16, height=16, fps=25)

    monkeypatch.setattr(tasks, "download_source", download)
    monkeypatch.setattr(animated, "prepare", encode)
    monkeypatch.setattr(ffmpeg, "probe", probe)
    sid = store.source_id_for("animated-offline-test")
    job = jobs.Job(id="cover-prepare", type="prepare")
    result = await tasks.fetch_source(job, source_id=sid, url="https://p1.music.126.net/cover.jpg", image=True)
    assert result.duration == 0.32 and len(prepared) == 1
    assert not list(tmp_path.glob("*.image"))
    assert await tasks.fetch_source(job, source_id=sid, image=True) is result
    assert len(prepared) == 1
