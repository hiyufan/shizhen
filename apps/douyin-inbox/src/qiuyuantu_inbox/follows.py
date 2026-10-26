"""新粉丝扫描与回关单路调度；媒体队列与回关暂停状态相互独立。"""

import asyncio
import contextlib
import logging
import time

from .follow_browser import FollowBrowser

log = logging.getLogger(__name__)


class FollowBack:
    def __init__(self, engine):
        self.engine = engine
        self.store, self.settings = engine.store, engine.settings
        self.client = FollowBrowser(engine.browser)
        self.installed = False

    async def discover(self) -> None:
        scan = await asyncio.to_thread(self.store.follow_scan, time.time())
        baseline = await asyncio.to_thread(self.store.get_meta, "follow_baseline")
        if baseline and scan["cursor"] < scan["ceiling"]:
            head = await self.client.call("page", cursor=int(time.time()))
            await asyncio.to_thread(self.store.record_follow_page, head, time.time())
        for _ in range(self.settings.follow_scan_pages):
            if self.engine.stopped.is_set() or await self.engine.paused():
                return
            page = await self.client.call("page", cursor=scan["cursor"])
            await asyncio.to_thread(self.store.record_follow_page, page, time.time(), scan)
            if not page["more"]:
                break
            scan = scan | {"cursor": page["cursor"]}
        await asyncio.to_thread(self.store.set_meta, "follow_scan_error", "")

    async def reconcile(self) -> None:
        for job in await asyncio.to_thread(self.store.follow_checks):
            relation = await self.client.call("relation", user=job)
            if relation["following"] > 0 and relation["follower"] == 1 and not relation["blocked"]:
                state = {1: "followed", 2: "mutual"}.get(relation["following"], "requested")
                await asyncio.to_thread(self.store.finish_follow, job, state, time.time())
            else:
                await asyncio.to_thread(self.store.checked_follow, job, time.time())
        await asyncio.to_thread(self.store.release_follow_pause)

    async def submit_once(self) -> None:
        if not self.installed:
            await self.client.call("install")
            self.installed = True
        if not await self.client.call("idle"):
            return
        job = await asyncio.to_thread(self.store.claim_follow, time.time())
        if not job:
            return
        submitted = False
        try:
            async with self.engine.ui:
                if not await asyncio.to_thread(self.store.begin_follow, job, time.time()):
                    return
                submitted = True
                result = await self.client.follow(job)
            error = f"platform_{result['code']}" if isinstance(result.get("code"), int) else ""
            await asyncio.to_thread(self.store.finish_follow, job, result["state"], time.time(), error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if submitted:
                await asyncio.to_thread(self.store.finish_follow, job, "uncertain", time.time(), type(exc).__name__)
            else:
                await asyncio.to_thread(self.store.defer_follow, job)
            raise

    async def tick(self) -> None:
        try:
            await self.discover()
        except BufferError:
            # 反压保留当前页，已入队的任务仍须继续消化，否则队列无法腾出容量。
            await asyncio.to_thread(self.store.set_meta, "follow_scan_error", "queue_full")
        await self.reconcile()
        await self.submit_once()

    async def loop(self) -> None:
        await asyncio.to_thread(self.store.recover_follows, time.time())
        try:
            while not self.engine.stopped.is_set():
                if not await self.engine.paused():
                    try:
                        await self.tick()
                    except Exception as exc:
                        # 不输出平台响应、用户资料或凭证，也不暂停媒体处理。
                        await asyncio.to_thread(self.store.set_meta, "follow_scan_error", type(exc).__name__)
                        log.warning("follow workflow stopped this cycle: %s", type(exc).__name__)
                await self.engine.delay(self.settings.follow_poll_seconds)
        finally:
            if self.installed:
                with contextlib.suppress(Exception):
                    await self.client.call("close")
