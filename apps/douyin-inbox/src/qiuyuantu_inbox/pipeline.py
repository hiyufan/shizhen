"""有界预上传、单一界面提交者与独立回执观察；未知发送不重试。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time

from .browser import BrowserUnavailable, acknowledged
from .media import UnsupportedMedia, blocking, file_md5
from .native_bridge import NativeBridge

log = logging.getLogger(__name__)


class NativePipeline:
    def __init__(self, engine):
        self.engine = engine
        self.store, self.browser, self.settings = engine.store, engine.browser, engine.settings
        self.bridge = NativeBridge(self.browser, engine.ui)
        self.inflight: dict[str, dict] = {}

    async def install(self) -> None:
        next_send = float(await asyncio.to_thread(self.store.get_meta, "next_send_at", "0"))
        cooldown = float(await asyncio.to_thread(self.store.get_meta, "send_cooldown", "0"))
        async with self.engine.ui:
            await self.bridge.install(next_send, cooldown)

    async def files(self, part: dict) -> list:
        files = self.engine.processor.files(part) if part.get("files") else []
        if files and [await blocking(file_md5, file) for file in files] != part["md5"]:
            raise UnsupportedMedia("缓存文件已变化")
        return files

    async def plan_once(self) -> None:
        retired = await asyncio.to_thread(self.store.retire_outbound)
        for row in retired:
            await self.bridge.forget(row | {"handle": row["handle"] or row["token"]})
        pending = await asyncio.to_thread(self.store.unplanned_outbound, self.engine.scope)
        for row in pending:
            try:
                files = await self.files(json.loads(row["payload"]))
                size = sum(file.stat().st_size for file in files)
                if size > self.settings.upload_bytes:
                    raise UnsupportedMedia("上传字节池不足")
                await asyncio.to_thread(self.store.plan_outbound, row, size, time.time())
            except (UnsupportedMedia, OSError, ValueError):
                await asyncio.to_thread(self.store.fail_outbound_plan, row, time.time())
        if retired or pending:
            self.engine.wakes.notify("upload", "dispatch", "warmup")

    async def plan_loop(self) -> None:
        while not self.engine.stopped.is_set():
            version = self.engine.wakes.version("plan")
            if not await self.engine.paused():
                await self.plan_once()
            await self.engine.wakes.wait("plan", version, 0.5)

    async def upload_once(self) -> bool:
        row = await asyncio.to_thread(self.store.claim_upload, time.time(), self.engine.scope)
        if not row:
            return False
        started = time.monotonic()
        try:
            files = await self.files(json.loads(row["payload"]))
            result = await self.bridge.upload(row, files)
            accepted = await asyncio.to_thread(self.store.uploaded, row, result, time.time())
            if not accepted:
                await self.bridge.forget(row | result)
            else:
                values = {
                    "preupload": time.monotonic() - started,
                    "upload_stream_seconds": result.get("upload_seconds", 0),
                    "cache_hits": result.get("cache_hits", 0),
                }
                await asyncio.to_thread(self.store.outbound_measure, row, values, time.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await asyncio.to_thread(self.store.upload_failed, row, time.time())
            reason = re.search(r"(?:outbound|native)_[a-z_]+", str(exc))
            log.warning(
                "inbox preupload failed: job=%s reason=%s", row["job"][:12], reason[0] if reason else type(exc).__name__
            )
        finally:
            self.engine.wakes.notify("upload", "dispatch", "warmup")
        return True

    async def upload_loop(self) -> None:
        while not self.engine.stopped.is_set():
            version = self.engine.wakes.version("upload")
            if await self.engine.paused() or not await self.upload_once():
                await self.engine.wakes.wait("upload", version, 0.5)

    async def finish_job(self, job: dict) -> None:
        parts = await asyncio.to_thread(self.store.parts, job)
        state = "ready" if any(part["state"] != "sent" for part in parts) else "sent"
        await asyncio.to_thread(self.store.finish, job, state, time.time())
        self.engine.wakes.notify("events", "plan", "upload", "dispatch", "warmup")

    async def fallback(self, job: dict, row: dict, part: dict) -> None:
        # 原生相册转发复用已验证的 UI 路径；窗口排空后执行，不与 detached 提交交叉。
        if self.inflight:
            await asyncio.to_thread(self.store.finish, job, "ready", time.time())
            await self.engine.delay(0.25)
            return
        payload = json.loads(part["payload"])
        async with self.engine.ui:
            if await self.engine.paused():
                await asyncio.to_thread(self.store.finish, job, "ready", time.time())
                return
            await self.browser.open(job["conversation"])
            await self.browser.guard(job["conversation"])
            await asyncio.to_thread(self.store.begin_outbound, job, row, time.time())
            started = time.monotonic()
            receipt = await self.browser.send(job["conversation"], payload, [])
            if len(receipt) != 1:
                raise BrowserUnavailable("outbound_fallback_receipt_changed")
            await asyncio.to_thread(self.store.acknowledge_outbound, job, row, receipt[0], time.time())
            await asyncio.to_thread(self.store.charge_dispatch, job, payload, time.monotonic() - started)
            await asyncio.to_thread(self.store.set_meta, "last_send", str(time.time()))
        await self.finish_job(job)
        await asyncio.to_thread(self.store.sync_outbound_clock, time.time() + self.settings.send_interval, 0)
        # GUI 路径没有走 SDK 包装器，仍要把其发送预算同步给页面提交门。
        await self.install()

    async def dispatch_once(self) -> bool:
        job = await asyncio.to_thread(self.store.claim, "ready", time.time(), self.engine.scope)
        if not job:
            return False
        try:
            part = next(p for p in await asyncio.to_thread(self.store.parts, job) if p["state"] != "sent")
            row = await asyncio.to_thread(self.store.outbound_part, job, part["number"])
            payload = json.loads(part["payload"])
            if payload.get("forward_source"):
                await self.fallback(job, row, part)
                return True
            if payload.get("files") and not await self.bridge.ready(row):
                await asyncio.to_thread(self.store.invalidate_upload, row, time.time())
                await asyncio.to_thread(self.store.finish, job, "ready", time.time())
                return True
            await self.submit(job, row, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.delivery_failure(job, exc)
        return True

    async def submit(self, job: dict, row: dict, payload: dict) -> None:
        waiting = time.monotonic()
        async with self.engine.ui:
            ui_wait = time.monotonic() - waiting
            if await self.engine.paused():
                await asyncio.to_thread(self.store.finish, job, "ready", time.time())
                return
            opening = time.monotonic()
            await self.browser.open(job["conversation"])
            await self.browser.guard(job["conversation"])
            conversation_open = time.monotonic() - opening
            await asyncio.to_thread(self.store.begin_outbound, job, row, time.time())
            started = time.monotonic()
            await asyncio.wait_for(self.bridge.submit(job, row, payload), timeout=self.settings.connect_timeout)
            elapsed = time.monotonic() - started
        await asyncio.to_thread(self.store.charge_dispatch, job, payload, elapsed)
        await asyncio.to_thread(
            self.store.outbound_measure,
            row,
            {"ui_submit": elapsed, "ui_wait": ui_wait, "conversation_open": conversation_open},
            time.time(),
        )
        self.inflight[row["client_id"]] = {
            "job": job,
            "row": row,
            "payload": payload,
            "started": time.time(),
        }
        self.engine.wakes.notify("observe")

    async def delivery_failure(self, job: dict, exc: Exception) -> None:
        parts = await asyncio.to_thread(self.store.parts, job)
        uncertain = any(part["state"] == "sending" for part in parts)
        await asyncio.to_thread(self.store.finish, job, "uncertain" if uncertain else "ready", time.time())
        if uncertain or not await self.engine.paused():
            await self.engine.pause("delivery_requires_review" if uncertain else "browser_requires_attention")
        log.warning("inbox native delivery stopped: job=%s reason=%s", job["id"][:12], type(exc).__name__)

    async def dispatch_loop(self) -> None:
        while not self.engine.stopped.is_set():
            version = self.engine.wakes.version("dispatch")
            if await self.engine.paused() or not await self.dispatch_once():
                await self.engine.wakes.wait("dispatch", version, 0.25)

    async def accept_receipt(self, entry: dict, observed: dict) -> bool:
        job, row, part = entry["job"], entry["row"], entry["payload"]
        receipt = observed.get("row")
        if not receipt:
            return False
        if receipt.get("client_id") != row["client_id"]:
            raise BrowserUnavailable("outbound_receipt_client_changed")
        expected_type = 7 if "text" in part else 30 if part.get("video") else 27
        if receipt.get("type") != expected_type:
            raise BrowserUnavailable("outbound_receipt_type_changed")
        if part.get("files") and not part.get("video") and not part.get("live_pair"):
            expected_awe = 2703 if part["files"][0].lower().endswith(".gif") else 2702
            if receipt.get("awe_type") != expected_awe:
                raise BrowserUnavailable("outbound_receipt_image_type_changed")
        ids = acknowledged([receipt], part, job["conversation"], set())
        if len(ids) != 1:
            raise BrowserUnavailable("outbound_receipt_content_changed")
        await asyncio.to_thread(self.store.acknowledge_outbound, job, row, ids[0], time.time())
        for phase, seconds in observed.get("timings", {}).items():
            await asyncio.to_thread(self.store.measure, job, phase, max(0, seconds))
        await asyncio.to_thread(self.store.set_meta, "last_send", str(time.time()))
        await self.finish_job(job)
        if observed.get("attempts") != 1:
            await self.engine.pause("sdk_submission_gate_requires_attention")
        return True

    async def observe_once(self) -> None:
        if not self.inflight:
            return
        rows = await self.bridge.poll(list(self.inflight))
        for observed in rows:
            client_id = observed["client_id"]
            entry = self.inflight.get(client_id)
            if not entry:
                continue
            await asyncio.to_thread(
                self.store.sync_outbound_clock, observed.get("next_submit", 0), observed.get("cooldown", 0)
            )
            metrics = observed.get("timings", {}) | {
                key: observed.get(key, 0) for key in ("attempts", "retry_blocked", "sdk_started", "event_receipt")
            }
            await asyncio.to_thread(self.store.outbound_measure, entry["row"], metrics, time.time())
            try:
                complete = await self.accept_receipt(entry, observed)
                if not complete and time.time() - entry["started"] < self.settings.ack_timeout:
                    continue
                if not complete:
                    raise BrowserUnavailable("outbound_ack_timeout")
            except Exception as exc:
                await self.delivery_failure(entry["job"], exc)
            self.inflight.pop(client_id, None)
            # 持久化先于释放页面记录；未结束的 SDK task 仍保留重试围栏。
            await self.bridge.forget(entry["row"])

    async def observe_loop(self) -> None:
        while not self.engine.stopped.is_set():
            version = self.engine.wakes.version("observe")
            try:
                await self.observe_once()
            except Exception as exc:
                for entry in list(self.inflight.values()):
                    await self.delivery_failure(entry["job"], exc)
                self.inflight.clear()
            await self.engine.wakes.wait("observe", version, 0.25)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.bridge.close()
        # 退出不重置发送意图。下一次 Store.recover 会暂停并要求核对未知回执。
