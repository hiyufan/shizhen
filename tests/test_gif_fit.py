"""GIF 压到目标体积：按降级顺序估参数，超了重做，压不进去交最小的那份。不跑真 ffmpeg。"""

import asyncio
import contextlib
from pathlib import Path

from parse_video_py.convert import ffmpeg, jobs, store, tasks


def test_first_steps_cost_little_quality():
    # 只超一点：减颜色就够，宽度和帧率不动
    assert tasks._shrink_gif(480, 12, 256, 0.95) == (480, 12, 128)
    # 超两倍多：颜色、帧率先降，再按需要缩宽度，宽度不低于 200
    w, fps, colors = tasks._shrink_gif(480, 12, 256, 0.43)
    assert (fps, colors) == (10, 128) and 200 <= w < 480 and w % 8 == 0


def test_floors_and_user_settings_are_respected():
    assert tasks._shrink_gif(160, 8, 64, 0.1) == (160, 8, 64)  # 都到底了
    w, fps, colors = tasks._shrink_gif(320, 5, 256, 0.3)
    assert fps == 5  # 用户自己设的 5 fps 不会被抬到 8
    assert tasks._shrink_gif(120, 12, 256, 0.1)[0] == 120  # 宽度也一样


def _fake_gif(monkeypatch, tmp_path, calls):
    async def make_gif(src, dst, *, start, duration, fps, width, dither, speed, colors, on_progress=None):
        calls.append((width, fps, colors))
        # 粗略模拟：体积 ∝ 宽^1.7 × 帧率 × 时长 × 颜色系数
        size = int(width**1.7 * fps * duration * {256: 1, 128: 0.81, 64: 0.65}[colors] * 0.6)
        Path(dst).write_bytes(b"\0" * size)

    monkeypatch.setattr(ffmpeg, "make_gif", make_gif)
    monkeypatch.setattr(tasks.config, "OUTPUTS_DIR", tmp_path)


def _run(duration, **kw):
    src = store.Source(id="s", path="/x.mp4", title="t", duration=duration, width=1280, height=720)
    job = jobs.Job(id="j", type="gif")
    asyncio.run(tasks.convert(job, src=src, fmt="gif", opts=tasks.ConvertOptions(**kw)))
    return job, Path(job.result_path).stat().st_size


def test_regenerates_until_under_target(monkeypatch, tmp_path):
    calls = []
    _fake_gif(monkeypatch, tmp_path, calls)
    job, size = _run(5, fps=12, width=480, max_bytes=1_000_000)
    assert len(calls) == 2 and size <= 1_000_000
    assert job.extra["fits"] is True and job.extra["width"] == calls[-1][0]


def test_gives_smallest_when_target_unreachable(monkeypatch, tmp_path):
    calls = []
    _fake_gif(monkeypatch, tmp_path, calls)
    job, size = _run(30, fps=12, width=480, end=30, max_bytes=100_000)
    assert calls[-1] == (160, 8, 64) and len(calls) <= tasks.GIF_FIT_ATTEMPTS + 1
    assert job.extra["fits"] is False and size > 100_000


def test_no_target_means_one_pass_with_user_settings(monkeypatch, tmp_path):
    calls = []
    _fake_gif(monkeypatch, tmp_path, calls)
    job, _ = _run(5, fps=12, width=480)
    assert calls == [(480, 12, 256)] and job.extra["fits"] is None


def test_every_ffmpeg_input_is_local_file_only(monkeypatch):
    # 不可信媒体里引用的网络地址不让 ffmpeg 去连：每个 -i 前面都要有 -protocol_whitelist file
    seen = []

    class _Stop(Exception):
        pass

    async def fake_exec(*cmd, **_):
        seen.append(list(cmd))
        raise _Stop

    monkeypatch.setattr(ffmpeg, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg.asyncio, "create_subprocess_exec", fake_exec)
    calls = (
        ffmpeg.probe("a.mp4"),
        ffmpeg.make_gif("a.mp4", "o.gif", start=0, duration=1, fps=10, width=320),
        ffmpeg.encode_segment("a.mp4", "o.mov", start=0, duration=1),
        ffmpeg.remux_live("a.mp4", "o.mov", identifier="X"),
        ffmpeg.extract_frame("a.mp4", "o.jpg", at=0),
        ffmpeg.filmstrip("a.mp4", "o.jpg", duration=1),
    )
    for coro in calls:
        with contextlib.suppress(_Stop):
            asyncio.run(coro)
    assert len(seen) == len(calls)
    for cmd in seen:
        idx = [i for i, arg in enumerate(cmd) if arg == "-i"]
        assert idx and all(cmd[i - 2 : i] == ["-protocol_whitelist", "file"] for i in idx), cmd


def _filmstrip_cmd(monkeypatch, duration: float) -> list[str]:
    seen = []

    class _Stop(Exception):
        pass

    async def fake_exec(*cmd, **_):
        seen.append(list(cmd))
        raise _Stop

    monkeypatch.setattr(ffmpeg, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg.asyncio, "create_subprocess_exec", fake_exec)
    with contextlib.suppress(_Stop):
        asyncio.run(ffmpeg.filmstrip("a.mp4", "o.jpg", duration=duration, frames=16))
    return seen[0]


def test_long_video_filmstrip_decodes_keyframes_only(monkeypatch):
    # 3.5 分钟：每格隔 13 秒，只解关键帧（5.9 秒 → 0.4 秒）；-skip_frame 是输入选项，要在 -i 前面
    cmd = _filmstrip_cmd(monkeypatch, 212)
    assert cmd.index("-skip_frame") < cmd.index("-i") and cmd[cmd.index("-skip_frame") + 1] == "nokey"


def test_short_video_filmstrip_decodes_every_frame(monkeypatch):
    # 49 秒的抖音只有 11 个关键帧，只解关键帧的话 16 格会重复、缺格
    assert "-skip_frame" not in _filmstrip_cmd(monkeypatch, 49.5)
