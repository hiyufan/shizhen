"""备用出口换地址：只在配置了控制口时生效，同一时刻只换一次，换得越勤间隔越长。"""

import asyncio

import pytest

from qiuyuantu.convert import fallback_exit, net, tasks

BOT = "ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(fallback_exit, "_state", fallback_exit._State())
    fallback_exit._lock.cache_clear()
    monkeypatch.delenv("PARSE_VIDEO_FALLBACK_RENEW", raising=False)


class _Control:
    """替身控制口：记下收到的每一行，按 replies 依次回答（用完后一直回最后一个）。"""

    def __init__(self, *replies: str, delay: float = 0) -> None:
        self.replies, self.delay, self.received = list(replies), delay, []

    async def _serve(self, reader, writer):
        self.received.append((await reader.readline()).decode().strip())
        await asyncio.sleep(self.delay)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        writer.write(f"{reply}\n".encode())
        await writer.drain()
        writer.close()

    async def start(self, monkeypatch) -> None:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        monkeypatch.setenv("PARSE_VIDEO_FALLBACK_RENEW", f"127.0.0.1:{port}")


async def test_off_without_control_port():
    assert await fallback_exit.renew(fallback_exit.generation()) is False
    assert fallback_exit.generation() == 0


async def test_renew_asks_control_port_and_bumps_generation(monkeypatch):
    control = _Control("ok")
    await control.start(monkeypatch)
    assert await fallback_exit.renew(0) is True
    assert control.received == ["renew"] and fallback_exit.generation() == 1
    assert "127.0.0.1" in net._trusted_hosts()


async def test_concurrent_failures_renew_once(monkeypatch):
    control = _Control("ok", delay=0.05)
    await control.start(monkeypatch)
    assert await asyncio.gather(*(fallback_exit.renew(0) for _ in range(5))) == [True] * 5
    assert control.received == ["renew"]


async def test_gap_grows_with_recent_renewals(monkeypatch):
    control = _Control("ok")
    await control.start(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(fallback_exit.time, "monotonic", lambda: clock[0])
    assert await fallback_exit.renew(0) is True
    for wait, expected in ((59, False), (1, True), (119, False), (1, True)):
        clock[0] += wait
        assert await fallback_exit.renew(fallback_exit.generation()) is expected
    # 一小时没再换过，下一次不用等
    clock[0] += fallback_exit._WINDOW
    assert fallback_exit._state.allowed(clock[0])
    assert len(control.received) == 3


@pytest.mark.parametrize("reply", ["failed", ""])
async def test_failed_renewal_is_not_worth_a_retry(monkeypatch, reply):
    control = _Control(reply)
    await control.start(monkeypatch)
    assert await fallback_exit.renew(0) is False
    assert fallback_exit.generation() == 0
    # 没换成也算一次，不能紧接着再去重连
    assert await fallback_exit.renew(0) is False
    assert len(control.received) == 1


async def test_unreachable_control_port(monkeypatch):
    server = await asyncio.start_server(lambda *_: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_RENEW", f"127.0.0.1:{port}")
    assert await fallback_exit.renew(0) is False


@pytest.mark.parametrize(
    ("errors", "renewed", "attempts", "raises"),
    [
        ([BOT], True, 2, None),
        ([BOT, BOT], True, 2, "not a bot"),  # 新出口也不行：只重试一次，交给后面的兜底
        ([BOT], False, 1, "not a bot"),
        (["HTTP Error 403: Forbidden"], True, 1, "403"),  # 不是按地址拦的，换出口没用
    ],
)
async def test_job_download_retries_once_after_renewal(monkeypatch, tmp_path, errors, renewed, attempts, raises):
    pending, calls, asked = list(errors), [], []

    async def download():
        calls.append(1)
        if pending:
            raise RuntimeError(pending.pop(0))
        return tmp_path / "v.mp4"

    async def renew(seen):
        asked.append(seen)
        return renewed

    monkeypatch.setattr(fallback_exit, "renew", renew)
    if raises:
        with pytest.raises(RuntimeError, match=raises):
            await tasks._renewing(download)
    else:
        assert await tasks._renewing(download) == tmp_path / "v.mp4"
    assert len(calls) == attempts and len(asked) == (0 if "403" in errors[0] else 1)
