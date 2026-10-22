"""解析阶段通知：只传阶段名，CLI 和没有订阅者的调用无需额外服务。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from contextvars import ContextVar

_ORDER = {
    "connecting": 0,
    "extracting": 1,
    "checking": 1,
    "fallback": 2,
    "queued": 2,
    "browser": 3,
    "downloading": 4,
    "merging": 5,
    "verifying": 6,
    "cached": 7,
}
_current: ContextVar[Progress | None] = ContextVar("parse_progress", default=None)


class Progress:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.stage = "connecting"
        self.fallback = False
        self.closed = False
        self.listeners: set[Callable[[dict], None]] = set()

    def snapshot(self) -> dict:
        return {"stage": self.stage, "fallback": self.fallback}

    def update(self, stage: str, fallback: bool = False) -> None:
        if self.closed or stage not in _ORDER:
            return
        if _ORDER[stage] < _ORDER[self.stage]:
            stage = self.stage
        state = {"stage": stage, "fallback": self.fallback or fallback}
        if state == self.snapshot():
            return
        self.stage, self.fallback = state["stage"], state["fallback"]
        for listener in tuple(self.listeners):
            listener(state.copy())

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self.listeners.add(listener)
        listener(self.snapshot())
        return lambda: self.listeners.discard(listener)

    def receive(self, state: dict) -> None:
        self.update(**state)


def current() -> Progress | None:
    return _current.get()


@contextlib.contextmanager
def track(progress: Progress):
    token = _current.set(progress)
    try:
        yield
    finally:
        _current.reset(token)


def emit(stage: str, *, fallback: bool = False) -> None:
    if (progress := current()) and not progress.closed:
        # yt-dlp 的线程池会继承 contextvars；所有订阅更新都回到请求的事件循环。
        with contextlib.suppress(RuntimeError):
            progress.loop.call_soon_threadsafe(progress.update, stage, fallback)
