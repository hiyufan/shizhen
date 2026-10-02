"""极简后台任务队列：下载 / 转换共用，带进度。"""

from __future__ import annotations

import asyncio
import functools
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

# 起别名：本模块自己有个 stats() 函数，同名会把统计模块覆盖掉
from .. import stats as usage_stats
from . import config
from .net import scrub

JobFn = Callable[["Job"], Awaitable[None]]


@dataclass
class Job:
    id: str
    type: str
    source_id: str | None = None
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result_path: str | None = None
    filename: str | None = None
    preview: str | None = None
    extra: dict = field(default_factory=dict)
    owner: str | None = None  # 客户端 IP，用于配额
    abort: bool = False  # 超时 / 取消后置位；跑在线程里的 yt-dlp 靠它自己停下来
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    task: asyncio.Task | None = None

    def set(self, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message

    def view(self) -> dict:
        size = None
        if self.result_path and Path(self.result_path).exists():
            size = Path(self.result_path).stat().st_size
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "filename": self.filename,
            "filesize": size,
            "source_id": self.source_id,
            "preview": self.preview,
            "extra": self.extra,
        }


_jobs: dict[str, Job] = {}
ReleaseFn = Callable[[], None]


@functools.cache
def _semaphore() -> asyncio.Semaphore:
    """同时跑的任务数上限；第一次用时才建，免得在 import 时绑到别的事件循环上。"""
    return asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def pending_count() -> int:
    return sum(1 for j in _jobs.values() if j.status in ("queued", "running"))


def find_pending(job_type: str, source_id: str) -> Job | None:
    """同一个来源正在准备中的任务；爆款链接几个人同时点，只下载一次。"""
    for j in _jobs.values():
        if j.type == job_type and j.source_id == source_id and j.status in ("queued", "running"):
            return j
    return None


def known_paths() -> set[Path]:
    """任务的结果文件和工作目录；清孤儿时要绕开。"""
    known: set[Path] = set()
    for j in _jobs.values():
        extra = j.extra or {}
        for p in (j.result_path, extra.get("preview_path"), extra.get("mov_path")):
            if p:
                known.add(Path(p).resolve())
        known.add((config.OUTPUTS_DIR / f"{j.id}_work").resolve())
    return known


def stats() -> dict:
    return {
        "pending": pending_count(),
        "running": sum(1 for j in _jobs.values() if j.status == "running"),
        "max_concurrent": config.MAX_CONCURRENT_JOBS,
        "max_queue": config.MAX_QUEUED_JOBS,
    }


class QueueFull(Exception):
    pass


def _timeout_message() -> str:
    limit = config.JOB_TIMEOUT_SECONDS
    human = f"{limit // 60} 分钟" if limit >= 60 else f"{limit} 秒"
    return f"超过 {human}还没做完，已放弃。试试缩短时长或降低尺寸"


async def _execute(job: Job, fn: JobFn) -> None:
    """跑任务本体，把结果记成 done / error。超时和取消都会置 abort，让线程里的 yt-dlp 自己停下来。"""
    job.status = "running"
    try:
        await asyncio.wait_for(fn(job), timeout=config.JOB_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        job.abort = True
        job.status, job.error = "error", _timeout_message()
    except asyncio.CancelledError:
        job.abort = True
        job.status, job.error = "error", "已取消"
        raise
    except Exception as e:  # noqa: BLE001 - 错误信息给页面显示
        traceback.print_exc()
        job.status, job.error = "error", scrub(str(e)) or e.__class__.__name__
    else:
        job.status, job.progress = "done", 1.0


def _finish(job: Job, on_release: ReleaseFn | None) -> None:
    job.finished_at = time.time()
    # 先还配额：后面记统计哪怕出错，也不能让这个 IP 的名额一直占着
    if on_release:
        on_release()
    usage_stats.record(
        "job",
        job.owner or "",
        source=job.type,
        ok=job.status == "done",
        reason=(job.error or "")[:32],
        ms=(job.finished_at - job.created_at) * 1000,
    )


def start(
    job_type: str,
    fn: JobFn,
    source_id: str | None = None,
    *,
    owner: str | None = None,
    on_release: ReleaseFn | None = None,
) -> Job:
    """排队执行 fn(job)。超过队列上限直接拒绝；单个任务超时会被取消并杀掉 ffmpeg。"""
    if pending_count() >= config.MAX_QUEUED_JOBS:
        raise QueueFull("服务器正忙，排队的任务太多了，请稍后再试")
    job = Job(id=uuid.uuid4().hex[:12], type=job_type, source_id=source_id, owner=owner)
    _jobs[job.id] = job

    async def runner() -> None:
        try:
            async with _semaphore():
                await _execute(job, fn)
        finally:
            _finish(job, on_release)

    job.task = asyncio.create_task(runner())
    return job


def cancel(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if job and job.task and not job.task.done():
        job.abort = True
        job.task.cancel()
        return True
    return False


def sweep() -> None:
    cutoff = time.time() - config.JOB_TTL_SECONDS
    for jid, job in list(_jobs.items()):
        if job.finished_at and job.finished_at < cutoff:
            _jobs.pop(jid, None)
            extra = job.extra or {}
            for path in (job.result_path, extra.get("preview_path"), extra.get("mov_path")):
                if path:
                    Path(path).unlink(missing_ok=True)
