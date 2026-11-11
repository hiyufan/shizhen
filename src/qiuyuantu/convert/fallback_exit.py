"""备用出口被平台按地址拦下时，让它换一个出口地址。

出口容器在内部控制口收一行 renew，断开重连后回一行结果（docker/warp/renew.sh）。这里只管去重和节流：
同一时刻只换一次，排在后面的请求直接重试；一小时内换得越多，下一次要等得越久，免得新地址也不行时反复重连。
"""

import asyncio
import functools
import logging
import time

from ..utils import fallback_renew

log = logging.getLogger("uvicorn.error")

_TIMEOUT = 25  # 重连通常一两秒；容器那边最多等十几秒
_WINDOW = 3600
_BASE_GAP = 30
_MAX_GAP = 900


class _State:
    def __init__(self) -> None:
        self.generation = 0
        self.attempts: list[float] = []

    def allowed(self, now: float) -> bool:
        self.attempts = [at for at in self.attempts if now - at < _WINDOW]
        if not self.attempts:
            return True
        return now - self.attempts[-1] >= min(_MAX_GAP, _BASE_GAP * 2 ** len(self.attempts))


_state = _State()


@functools.cache
def _lock() -> asyncio.Lock:
    return asyncio.Lock()


def generation() -> int:
    """出口换过几次。发请求前记下来，失败后交给 renew()，别人已经换过就不再重复换。"""
    return _state.generation


async def renew(seen: int) -> bool:
    """返回 True 表示出口在 seen 之后换过，值得再试一次；没配控制口、还在间隔内或没换成都是 False。"""
    target = fallback_renew()
    if not target:
        return False
    async with _lock():
        if _state.generation != seen:
            return True
        if not _state.allowed(now := time.monotonic()):
            return False
        _state.attempts.append(now)
        reply = await _ask(*target)
        # recent：容器刚换过（比如应用重启后忘了自己换过），同样值得重试
        renewed = reply in ("ok", "recent")
        if renewed:
            _state.generation += 1
        log.warning("YouTube 备用出口被拦，换出口：%s", reply or "控制口没回应")
        return renewed


async def _ask(host: str, port: int) -> str:
    try:
        return await asyncio.wait_for(_exchange(host, port), _TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return ""


async def _exchange(host: str, port: int) -> str:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(b"renew\n")
        await writer.drain()
        return (await reader.readline()).decode("ascii", "replace").strip()
    finally:
        writer.close()
