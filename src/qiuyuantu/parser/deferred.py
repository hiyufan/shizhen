"""可延后的解析补充：网页流式接口先返回基础结果，补充完成后再推送更新。

没有调用方登记收集时（JSON 接口、命令行、账号助手）当场等待并合并，结果与原来一致。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from typing import Any

from .. import progress

Pending = list[tuple[asyncio.Task, Callable[[Any], None]]]
_collector: ContextVar[Pending | None] = ContextVar("deferred_enrichment", default=None)


@contextlib.contextmanager
def collect() -> Iterator[Pending]:
    pending: Pending = []
    token = _collector.set(pending)
    try:
        yield pending
    finally:
        _collector.reset(token)


async def _quietly(work: Awaitable[Any]) -> Any:
    # 基础结果已经发出，后台补充不能再把页面的解析阶段拨回「核对中」。
    with progress.track(None):
        return await work


async def later(work: Awaitable[Any], apply: Callable[[Any], None]) -> None:
    pending = _collector.get()
    if pending is None:
        apply(await work)
        return
    pending.append((asyncio.create_task(_quietly(work)), apply))


async def finish(pending: Pending, timeout: float) -> None:
    """等待补充并按登记顺序合并；超时或失败的补充直接放弃，基础结果仍然可用。"""
    tasks = [task for task, _apply in pending]
    try:
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
    finally:
        cancel(pending)
        await asyncio.gather(*tasks, return_exceptions=True)
    for task, apply in pending:
        if not task.cancelled() and task.exception() is None:
            apply(task.result())


def cancel(pending: Pending) -> None:
    for task, _apply in pending:
        if not task.done():
            task.cancel()
