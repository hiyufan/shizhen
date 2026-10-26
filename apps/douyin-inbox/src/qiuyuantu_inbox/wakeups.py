"""进程内合并通知；版本号覆盖检查与等待之间的竞态，多 worker 独立等待。

通知只提示重新查持久队列，不承载任务。超时补查仍负责重试期限、丢通知和外部状态变化。
所有方法由同一事件循环调用，数据库线程提交后再通知。
"""

import asyncio
from collections import defaultdict


class Wakeups:
    def __init__(self):
        self.versions: dict[str, int] = defaultdict(int)
        self.waiters: dict[str, set[asyncio.Future]] = defaultdict(set)

    def version(self, stage: str) -> int:
        return self.versions[stage]

    def notify(self, *stages: str) -> None:
        for stage in stages:
            self.versions[stage] += 1
            for waiter in self.waiters[stage]:
                if not waiter.done():
                    waiter.set_result(None)

    async def wait(self, stage: str, version: int, seconds: float) -> None:
        if self.version(stage) != version:
            return
        waiter = asyncio.get_running_loop().create_future()
        self.waiters[stage].add(waiter)
        try:
            await asyncio.wait_for(waiter, timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self.waiters[stage].discard(waiter)
