"""Ugoira 不可信 ZIP 的边界及毫秒帧时长。"""

import io
import threading
import zipfile

import imageio_ffmpeg
import pytest
from PIL import Image

from qiuyuantu.convert import ffmpeg, ugoira
from qiuyuantu.parser.base import MediaAsset, VideoInfo


def jpeg():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, "JPEG")
    return output.getvalue()


def archive(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, jpeg())


def test_frame_order_duration_and_end_hold_are_preserved(tmp_path):
    source = tmp_path / "frames.zip"
    archive(source, ["000001.jpg", "000000.jpg"])
    frames = [{"file": "000000.jpg", "delay": 120}, {"file": "000001.jpg", "delay": 130}]
    directory = tmp_path / "frames"
    directory.mkdir()
    assert ugoira._write_frames(source, directory, frames, threading.Event()) == 0.25
    listing = (directory / "frames.txt").read_text()
    assert "duration 0.120000\nfile 'frame0001.png'" in listing
    assert listing.endswith("file 'frame0001.png'\noption framerate 1000\n")
    assert len(list(directory.glob("*.png"))) == 2


@pytest.mark.parametrize("names", [["../000000.jpg"], ["000000.jpg", "extra.jpg"], ["000001.jpg"]])
def test_extra_missing_or_traversal_entries_are_rejected(tmp_path, names):
    source = tmp_path / "frames.zip"
    archive(source, names)
    with zipfile.ZipFile(source) as zf, pytest.raises(ValueError):
        ugoira._entries(zf, [{"file": "000000.jpg", "delay": 100}])
    assert not (tmp_path.parent / "000000.jpg").exists()


def test_duplicate_zip_names_are_rejected(tmp_path):
    source = tmp_path / "frames.zip"
    with pytest.warns(UserWarning):
        archive(source, ["000000.jpg", "000000.jpg"])
    with zipfile.ZipFile(source) as zf, pytest.raises(ValueError):
        ugoira._entries(zf, [{"file": "000000.jpg", "delay": 100}])


async def test_cancellation_waits_for_decoder_before_directory_cleanup(monkeypatch, tmp_path):
    import asyncio

    entered, finished = threading.Event(), threading.Event()

    def decoder(source, directory, frames, stopped):
        entered.set()
        stopped.wait(2)
        (directory / "last-write").touch()
        finished.set()

    monkeypatch.setattr(ugoira, "_write_frames", decoder)
    task = asyncio.create_task(ugoira._decode(tmp_path / "zip", tmp_path, []))
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set() and (tmp_path / "last-write").exists()


async def test_mp4_preparation_pads_odd_frames_without_resampling(monkeypatch, tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (17, 19), "white").save(image, "PNG")
    frames = [{"file": "000000.png", "delay": 100}, {"file": "000001.png", "delay": 100}]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for frame in frames:
            archive.writestr(frame["file"], image.getvalue())

    async def metadata(_parser, _url):
        return VideoInfo(
            "",
            "",
            assets=[MediaAsset("https://i.pximg.net/frames.zip", kind="archive", ext="zip")],
            animation_frames=frames,
        )

    async def fetch(_url, path, *, limit):
        assert len(buffer.getvalue()) < limit
        path.write_bytes(buffer.getvalue())

    monkeypatch.setattr(ugoira.Pixiv, "parse_share_url", metadata)
    monkeypatch.setattr(ugoira, "fetch_bytes", fetch)
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", imageio_ffmpeg.get_ffmpeg_exe)
    output = await ugoira.download("https://www.pixiv.net/artworks/42", tmp_path, "out", lambda *_: None)
    probe = await ffmpeg.probe(output)
    assert (probe.width, probe.height) == (18, 20)
    decoded = tmp_path / "frame.png"
    await ffmpeg.run(["-i", str(output), "-frames:v", "1", "-pix_fmt", "rgb24", str(decoded)])
    with Image.open(decoded) as result:
        assert min(result.getpixel((8, 9))) > 230
        assert min(result.getpixel((16, 9))) > 230 and min(result.getpixel((8, 18))) > 230
        assert max(result.getpixel((17, 9))) < 32 and max(result.getpixel((8, 19))) < 32
