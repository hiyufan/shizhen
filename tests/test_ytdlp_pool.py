"""yt-dlp 跑在自己的线程池里，不占 asyncio 默认的池；跑起来还认得当前在解析哪个平台。"""

import asyncio
import threading

import pytest

from parse_video_py import utils
from parse_video_py.convert import net


def test_ytdlp_runs_in_its_own_pool_and_keeps_context():
    def work(x):
        return threading.current_thread().name, utils.current_source.get(), utils.proxy_for(), x

    async def run():
        utils.current_source.set("ytdlp")
        return await utils.run_ytdlp(work, 1)

    name, source, _, x = asyncio.run(run())
    assert name.startswith("yt-dlp") and source == "ytdlp" and x == 1


def test_busy_ytdlp_pool_does_not_block_default_pool():
    """yt-dlp 的池占满了，默认池（DNS 查询、统计落盘）照样有空。"""
    release = threading.Event()

    async def run():
        blockers = [asyncio.ensure_future(utils.run_ytdlp(release.wait, 5)) for _ in range(16)]
        await asyncio.sleep(0.05)
        try:
            return await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 1)
        finally:
            release.set()
            await asyncio.gather(*blockers)

    assert asyncio.run(run()) == "free"


def test_refuses_to_run_on_uvloop(monkeypatch):
    """uvloop 不经过 socket.getaddrinfo，SSRF 防线在它上面不生效，宁可起不来。"""
    fake_loop = type("Loop", (), {"__module__": "uvloop"})()
    monkeypatch.setattr(net, "_GUARD", True)
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)
    with pytest.raises(RuntimeError, match="uvloop"):
        net.check_event_loop()


def test_asyncio_loop_is_fine():
    async def run():
        net.check_event_loop()

    asyncio.run(run())
