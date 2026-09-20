"""极简后台任务队列：下载 / 转换共用，带进度。"""
from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import config
from .net import scrub


JobFn = Callable[["Job"], Awaitable[None]]


@dataclass
class Job:
    id: str
    type: str
    source_id: Optional[str] = None
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    result_path: Optional[str] = None
    filename: Optional[str] = None
    preview: Optional[str] = None
    extra: dict = field(default_factory=dict)
    owner: Optional[str] = None            # 客户端 IP，用于配额
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    task: Optional[asyncio.Task] = None

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
            "id": self.id, "type": self.type, "status": self.status,
            "progress": round(self.progress, 4), "message": self.message,
            "error": self.error, "filename": self.filename, "filesize": size,
            "source_id": self.source_id, "preview": self.preview, "extra": self.extra,
        }


_jobs: dict[str, Job] = {}
_sem: Optional[asyncio.Semaphore] = None
ReleaseFn = Callable[[], None]


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
    return _sem


def get(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def pending_count() -> int:
    return sum(1 for j in _jobs.values() if j.status in ("queued", "running"))


def stats() -> dict:
    return {
        "pending": pending_count(),
        "running": sum(1 for j in _jobs.values() if j.status == "running"),
        "max_concurrent": config.MAX_CONCURRENT_JOBS,
        "max_queue": config.MAX_QUEUED_JOBS,
    }


class QueueFull(Exception):
    pass


def start(job_type: str, fn: JobFn, source_id: str | None = None, *,
          owner: str | None = None, on_release: ReleaseFn | None = None) -> Job:
    """排队执行 fn(job)。超过队列上限直接拒绝；单个任务超时会被取消并杀掉 ffmpeg。"""
    if pending_count() >= config.MAX_QUEUED_JOBS:
        raise QueueFull("服务器正忙，排队的任务太多了，请稍后再试")
    job = Job(id=uuid.uuid4().hex[:12], type=job_type, source_id=source_id, owner=owner)
    _jobs[job.id] = job

    async def runner() -> None:
        try:
            async with _semaphore():
                job.status = "running"
                try:
                    await asyncio.wait_for(fn(job), timeout=config.JOB_TIMEOUT_SECONDS)
                    job.status = "done"
                    job.progress = 1.0
                except asyncio.TimeoutError:
                    job.status = "error"
                    limit = config.JOB_TIMEOUT_SECONDS
                    human = f"{limit // 60} 分钟" if limit >= 60 else f"{limit} 秒"
                    job.error = f"超过 {human}还没做完，已放弃。试试缩短时长或降低尺寸"
                except asyncio.CancelledError:
                    job.status = "error"
                    job.error = "已取消"
                    raise
                except Exception as e:  # noqa: BLE001 - surfaced to the UI
                    traceback.print_exc()
                    job.status = "error"
                    job.error = scrub(str(e)) or e.__class__.__name__
        finally:
            job.finished_at = time.time()
            if on_release:
                on_release()

    job.task = asyncio.create_task(runner())
    return job


def cancel(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if job and job.task and not job.task.done():
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
