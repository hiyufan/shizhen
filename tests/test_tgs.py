"""TGS 的有界解压、外部资源拒绝和渲染子进程回收。"""

import asyncio
import gzip
import json

import pytest

from qiuyuantu.convert import tgs


def source(tmp_path, **overrides):
    path = tmp_path / "sticker.tgs"
    data = {"w": 512, "h": 512, "fr": 60, "ip": 0, "op": 90, "layers": [], **overrides}
    path.write_bytes(gzip.compress(json.dumps(data).encode()))
    return path


@pytest.mark.parametrize(
    "change",
    [
        {"w": 100000},
        {"fr": 0},
        {"fr": float("nan")},
        {"op": 10000},
        {"h": -1},
        {"assets": [{"u": "file:///", "p": "private.png"}]},
        {"fonts": {"list": [{"name": "Private"}]}},
        {"assets": [{"p": "data:image/png;base64,anything"}]},
    ],
)
def test_tgs_rejects_external_resources_and_unbounded_animation(tmp_path, change):
    with pytest.raises(ValueError):
        tgs.animation_data(source(tmp_path, **change))


def test_tgs_limits_decompressed_data_before_json_parsing(tmp_path):
    path = tmp_path / "large.tgs"
    path.write_bytes(gzip.compress(b" " * 4_000_001))
    with pytest.raises(ValueError, match="解压"):
        tgs.animation_data(path)


def test_raw_lottie_json_keeps_timing_without_tgs_compressed_size_limit(tmp_path):
    path = tmp_path / "animation"
    data = {"w": 320, "h": 320, "fr": 60, "ip": 0, "op": 120, "layers": [], "nm": " " * 300_000}
    path.write_text(json.dumps(data))
    assert tgs.animation_data(path)["op"] == 120


@pytest.mark.parametrize("content", [b" " * 1_000_001, b"[]", b'{"assets":[{"p":"private.png"}]}'])
def test_raw_lottie_rejects_large_invalid_and_external_resources(tmp_path, content):
    path = tmp_path / "animation.json"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        tgs.animation_data(path)


async def test_raw_json_is_rendered_in_worker_instead_of_image_thread(tmp_path, monkeypatch):
    from qiuyuantu.convert import animated

    path = tmp_path / "animation"
    path.write_bytes(b'\xef\xbb\xbf  {"v":"5.5.7"}')
    called = []

    async def render(source, directory):
        called.append((source, directory))
        return 2.0

    monkeypatch.setattr(tgs, "render_frames", render)
    assert await animated._frames(path, tmp_path) == 2.0
    assert called == [(path, tmp_path)]


async def test_tgs_cancel_kills_and_reaps_render_worker(tmp_path, monkeypatch):
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = waited = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    process = Process()

    async def spawn(*args, **kw):
        assert "-m" in args and "qiuyuantu.convert.tgs" in args
        return process

    monkeypatch.setattr(tgs.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(tgs.render_frames(source(tmp_path), tmp_path))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.waited
