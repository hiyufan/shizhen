"""Ugoira 不可信 ZIP 的边界及毫秒帧时长。"""

import io
import threading
import zipfile

import pytest
from PIL import Image

from qiuyuantu.convert import ugoira


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
