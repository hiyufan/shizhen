"""GIF 压到目标体积：按降级顺序估参数，超了重做，压不进去交最小的那份。不跑真 ffmpeg。"""

import asyncio
from pathlib import Path

from parse_video_py.convert import ffmpeg, jobs, store, tasks


def test_first_steps_cost_little_quality():
    # 只超一点：减颜色就够，宽度和帧率不动
    assert tasks._shrink_gif(480, 12, 256, 0.95) == (480, 12, 128)
    # 超两倍多：颜色、帧率先降，再按需要缩宽度，宽度不低于 200
    w, fps, colors = tasks._shrink_gif(480, 12, 256, 0.43)
    assert (fps, colors) == (10, 128) and 200 <= w < 480 and w % 8 == 0


def test_floors_and_user_settings_are_respected():
    assert tasks._shrink_gif(160, 8, 64, 0.1) == (160, 8, 64)        # 都到底了
    w, fps, colors = tasks._shrink_gif(320, 5, 256, 0.3)
    assert fps == 5                                                  # 用户自己设的 5 fps 不会被抬到 8
    assert tasks._shrink_gif(120, 12, 256, 0.1)[0] == 120            # 宽度也一样


def _fake_gif(monkeypatch, tmp_path, calls):
    async def make_gif(src, dst, *, start, duration, fps, width, dither, speed, colors, on_progress=None):
        calls.append((width, fps, colors))
        # 粗略模拟：体积 ∝ 宽^1.7 × 帧率 × 时长 × 颜色系数
        size = int(width ** 1.7 * fps * duration * {256: 1, 128: .81, 64: .65}[colors] * 0.6)
        Path(dst).write_bytes(b"\0" * size)
    monkeypatch.setattr(ffmpeg, "make_gif", make_gif)
    monkeypatch.setattr(tasks.config, "OUTPUTS_DIR", tmp_path)


def _run(duration, **kw):
    src = store.Source(id="s", path="/x.mp4", title="t", duration=duration, width=1280, height=720)
    job = jobs.Job(id="j", type="gif")
    asyncio.run(tasks.convert(job, src=src, fmt="gif", **kw))
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
