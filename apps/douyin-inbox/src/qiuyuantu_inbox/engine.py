"""浏览器串行收发，媒体准备有界并发；队列状态全部落盘。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import replace

from .assets import AssetCoordinator, DependencyPending
from .browser import BrowserUnavailable
from .events import EventReader
from .media import (
    Asset,
    Processor,
    UnsupportedMedia,
    assets_from_snapshot,
    blocking,
    delivery_parts,
    file_md5,
    preparation_key,
)
from .models import Message, content_key, message_from_snapshot
from .pipeline import NativePipeline
from .store import Store
from .video_gif import gif_key, native_video_key

log = logging.getLogger(__name__)
EVENT_RECEIVE_HEARTBEAT = 15.0


class Engine:
    def __init__(self, store: Store, browser, scope: set[str], send: bool):
        self.store, self.browser = store, browser
        self.settings, self.scope, self.send_enabled = store.settings, scope, send
        self.processor = Processor(store, self.settings)
        self.assets = AssetCoordinator(store)
        self.ui = asyncio.Lock()
        self.stopped = asyncio.Event()
        self.events = EventReader(browser) if getattr(browser, "page", None) is not None else None
        self.event_receive_written = 0.0

    async def delay(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.stopped.wait(), timeout=seconds)

    async def paused(self) -> bool:
        return bool(await asyncio.to_thread(self.store.get_meta, "paused"))

    async def pause(self, reason: str) -> None:
        # 只记录分类，不把页面、签名地址或平台返回的原始异常写进公开日志。
        await asyncio.to_thread(self.store.set_meta, "paused", reason)
        log.warning("inbox paused: %s", reason)

    async def collect_once(self) -> bool:
        async with self.ui:
            conversations = await self.browser.conversations()
            candidates = []
            catchup_required = not getattr(self.browser, "conversation_scan_complete", True)
            for item in conversations:
                if self.scope and item["id"] not in self.scope:
                    continue
                cursor = await asyncio.to_thread(self.store.cursor, item["id"])
                # 网页重启后的懒加载会话可能尚无 lastMessage；不能把未知时间当成没有新消息。
                if not item["last_at"] or item["last_at"] >= cursor or item["unread"]:
                    candidates.append((cursor, item))
            catchup_required = catchup_required or len(candidates) > self.settings.conversations_per_poll
            for cursor, item in sorted(candidates, key=lambda pair: pair[0])[: self.settings.conversations_per_poll]:
                started = time.time()
                rows, complete = await self.browser.collect(item["id"], cursor - 60)
                if any(raw["conversation"] != item["id"] for raw in rows):
                    raise BrowserUnavailable("conversation_changed_during_read")
                await self.capture_rows(rows)
                if complete:
                    await asyncio.to_thread(self.store.checkpoint, item["id"], max(cursor, started - 60))
                else:
                    catchup_required = True
            await asyncio.to_thread(self.store.set_meta, "catchup_required", "1" if catchup_required else "")
            await asyncio.to_thread(self.store.set_meta, "last_receive", str(time.time()))
            return not catchup_required

    async def capture_rows(self, rows: list[dict]) -> None:
        messages = []
        floors = {}
        for raw in sorted(rows, key=lambda row: (row["created"], row["id"])):
            if self.scope and raw["conversation"] not in self.scope:
                continue
            if raw["conversation"] not in floors:
                floors[raw["conversation"]] = await asyncio.to_thread(self.store.message_floor, raw["conversation"])
            if (message := message_from_snapshot(raw)) and message.created >= floors[raw["conversation"]]:
                messages.append(message)
        await self.consume_inbound()
        for start in range(0, len(messages), 100):
            await asyncio.to_thread(self.store.capture, messages[start : start + 100], time.time())
            await self.consume_inbound()

    async def consume_inbound(self) -> None:
        for event in await asyncio.to_thread(self.store.inbound):
            await asyncio.to_thread(self.store.handle_inbound, event, time.time())

    async def event_once(self) -> dict:
        try:
            batch = await self.events.read()
        except Exception:
            batch = await self.events.install(self.scope)
            await asyncio.to_thread(self.store.set_meta, "event_gap", "1")
        await self.capture_rows([item["row"] for item in batch["rows"]])
        await self.events.ack(batch)
        # 事件每秒轮询一次；空批次只做状态展示用的心跳，降频写入，有消息时立即记录。
        if batch["rows"] or time.monotonic() - self.event_receive_written >= EVENT_RECEIVE_HEARTBEAT:
            await asyncio.to_thread(self.store.set_meta, "last_event_receive", str(time.time()))
            self.event_receive_written = time.monotonic()
        return batch

    async def event_loop(self) -> None:
        failures = 0
        while not self.stopped.is_set():
            reason = await asyncio.to_thread(self.store.get_meta, "paused")
            if reason in {"", "delivery_requires_review"}:
                try:
                    batch = await self.event_once()
                    await asyncio.to_thread(self.store.set_meta, "event_mode", "active")
                    await asyncio.to_thread(self.store.set_meta, "event_gap", "1" if batch["gaps"] else "")
                    failures = 0
                except Exception as exc:
                    failures += 1
                    await asyncio.to_thread(self.store.set_meta, "event_mode", "fallback")
                    log.warning("inbox events unavailable: %s", type(exc).__name__)
            await self.delay(min(60, 2 ** min(failures, 6)) if failures else self.settings.event_poll_seconds)

    async def collect_loop(self) -> None:
        failures = 0
        while not self.stopped.is_set():
            if not await self.paused():
                try:
                    batch = None
                    if self.events:
                        with contextlib.suppress(Exception):
                            batch = await self.events.read()
                    complete = await self.collect_once()
                    if batch and complete:
                        await self.events.scanned(batch)
                    failures = 0
                except Exception as exc:
                    failures += 1
                    log.warning("inbox receive failed: %s", type(exc).__name__)
                    if isinstance(exc, BrowserUnavailable) or failures >= 3:
                        await self.pause("browser_or_login_requires_attention")
            mode = await asyncio.to_thread(self.store.get_meta, "event_mode")
            await self.delay(self.settings.scan_seconds if mode == "active" else self.settings.poll_seconds)

    async def prepare_once(self) -> bool:
        job = await asyncio.to_thread(self.store.claim, "queued", time.time(), self.scope)
        if not job:
            return False
        started = time.monotonic()
        try:
            message = Message(**json.loads(job["payload"]))
            key, parts = await asyncio.wait_for(self.prepare_message(job, message), timeout=self.settings.job_timeout)
            await asyncio.to_thread(self.store.ready, job, key, parts, time.time())
        except asyncio.CancelledError:
            # processing 没有外部写入，重启后可以安全重新准备。
            raise
        except DependencyPending:
            pass
        except Exception as exc:
            await asyncio.to_thread(
                self.store.preparation_failed,
                job,
                type(exc).__name__,
                time.time(),
                not isinstance(exc, UnsupportedMedia),
            )
            log.warning("inbox prepare failed: job=%s reason=%s", job["id"][:12], type(exc).__name__)
        finally:
            await asyncio.to_thread(self.store.measure, job, "prepare", time.monotonic() - started)
        return True

    async def prepare_message(self, job: dict, message: Message) -> tuple[str, list[dict]]:
        selected = await asyncio.to_thread(self.store.selection, job)
        if selected:
            return await self.prepare_selected(job, selected)
        if message.kind in {"live", "photo"}:
            part = {"forward_source": message.id, "md5": [message.data["md5"]]}
            if message.kind == "live":
                part["live_vid"] = message.data["live_vid"]
                if message.data.get("live_md5"):
                    part["native_video_md5"] = message.data["live_md5"]
                return "", [{"offer_video": {"native_live": part, "files": [], "md5": [], "details": []}}]
            return "", [part]
        await asyncio.to_thread(self.store.pin_artifact, job, content_key(message))
        assets = await self.describe_source(job, message)
        if assets and assets["selectable"]:
            return assets.get("artifact", ""), [{"offer_video": assets.get("prepared", assets)}]
        resolved = [Asset(**row) for row in assets["assets"]] if assets else None
        await asyncio.to_thread(self.store.pin_artifact, job, content_key(message))
        key, manifest = await self.assets.run(
            job, content_key(message), lambda: self.processor.prepare(message, resolved)
        )
        unverified = message.data.get("comment_verified") is False or message.data.get("comment_unavailable")
        if unverified and all(detail.get("frames") == 1 for detail in manifest["details"]):
            return key, [{"text": "这条评论目前只拿到静态预览，尚未取得配套实况视频，暂时无法回传无水印 LIVE。"}]
        if manifest.get("live_photo_count") or (len(manifest["files"]) == 1 and manifest["files"][0].endswith(".mp4")):
            return key, [{"offer_video": manifest}]
        return key, delivery_parts(manifest, self.settings.photo_batch)

    async def describe_source(self, job: dict, message: Message) -> dict | None:
        if message.kind == "media":
            return None
        assets = None
        if message.kind == "work" and job["attempts"] == 1:
            async with self.ui:
                rows = await self.browser.work_media(message)
            assets = assets_from_snapshot(rows, self.settings)
        return await self.processor.describe(message, assets)

    async def prepare_selected(self, job: dict, selected: dict) -> tuple[str, list[dict]]:
        selected = await self.prepare_choice_source(job, selected)
        source = json.loads(selected["manifest"])
        original = (
            [source["native_live"]] if source.get("native_live") else delivery_parts(source, self.settings.photo_batch)
        )
        if selected["mode"] == "video":
            return selected["artifact"], original
        source = await self.resolve_motion(job, source)
        await asyncio.to_thread(self.store.pin_artifact, job, gif_key(source, self.settings))
        key, converted = await self.assets.run(
            job, gif_key(source, self.settings), lambda: self.encode_choice(job, source)
        )
        return key, (original if selected["mode"] == "both" else []) + delivery_parts(
            converted, self.settings.photo_batch
        )

    async def encode_choice(self, job: dict, source: dict) -> tuple[str, dict]:
        if self.settings.encode_workers == 1:
            return await self.assets.run(job, "encoder-slot", lambda: self.processor.gif_variant(source))
        return await self.processor.gif_variant(source)

    async def prepare_choice_source(self, job: dict, selected: dict) -> dict:
        source = json.loads(selected["manifest"])
        if not source.get("metadata_only"):
            return selected
        message = Message(**json.loads(job["payload"]))
        if time.time() - source["resolved_at"] > 120:
            refreshed = await self.describe_source(job, message)
            if refreshed.get("prepared"):
                await asyncio.to_thread(self.store.set_choice_source, job, refreshed["artifact"], refreshed["prepared"])
                return selected | {"artifact": refreshed["artifact"], "manifest": json.dumps(refreshed["prepared"])}
            if refreshed.get("live_photo_count") != source.get("live_photo_count"):
                raise UnsupportedMedia("原实况配对已变化，请重新分享")
            source = refreshed
        assets = [Asset(**row) for row in source["assets"]]
        variant = ""
        if selected["mode"] == "gif":
            assets = [replace(asset, live=False) for asset in assets if asset.video]
            variant = "motion"
        key = preparation_key(message, variant)
        await asyncio.to_thread(self.store.pin_artifact, job, key)
        key, manifest = await self.assets.run(
            job, key, lambda: self.processor.prepare(message, assets, variant=variant)
        )
        await asyncio.to_thread(self.store.set_choice_source, job, key, manifest)
        return selected | {"artifact": key, "manifest": json.dumps(manifest)}

    async def resolve_motion(self, job: dict, source: dict) -> dict:
        if not source.get("native_live") or source["files"]:
            return source
        message = Message(**json.loads(job["payload"]))
        if not message.data.get("live_md5"):
            raise UnsupportedMedia("原消息缺少可验证的视频摘要")
        await asyncio.to_thread(self.store.pin_artifact, job, native_video_key(message))
        key, downloaded = await self.assets.run(
            job, native_video_key(message), lambda: self.download_native_motion(message)
        )
        source = downloaded | {"native_live": source["native_live"]}
        await asyncio.to_thread(self.store.set_choice_source, job, key, source)
        return source

    async def download_native_motion(self, message: Message) -> tuple[str, dict]:
        async with self.ui:
            return await self.processor.native_motion(message, self.browser.native_motion)

    async def prepare_loop(self) -> None:
        while not self.stopped.is_set():
            if await self.paused() or not await self.prepare_once():
                await self.delay(1)

    async def _send_part(self, job: dict, part: dict) -> None:
        payload = json.loads(part["payload"])
        files = self.processor.files(payload) if payload.get("files") else []
        if files and [await blocking(file_md5, file) for file in files] != payload["md5"]:
            raise UnsupportedMedia("缓存文件已变化")
        await self.browser.open(job["conversation"])
        await self.browser.guard(job["conversation"])
        await asyncio.to_thread(self.store.start_part, job, part["number"], time.time())
        await asyncio.to_thread(self.store.set_meta, "last_attempt", str(time.time()))
        # 原生实况虽有两个上传文件，实际只提交一条消息。
        cost = 1 if payload.get("live_pair") else max(1, len(files))
        await asyncio.to_thread(
            self.store.set_meta, "next_send_at", str(time.time() + self.settings.send_interval * cost)
        )
        started = time.monotonic()
        receipt = await self.browser.send(job["conversation"], payload, files)
        await asyncio.to_thread(self.store.measure, job, "upload_ack", time.monotonic() - started)
        for phase, seconds in getattr(self.browser, "send_timings", {}).items():
            await asyncio.to_thread(self.store.measure, job, phase, seconds)
        await asyncio.to_thread(self.store.finish_part, job, part["number"], receipt)
        await asyncio.to_thread(self.store.charge_dispatch, job, payload, time.monotonic() - started)
        await asyncio.to_thread(self.store.set_meta, "last_send", str(time.time()))

    async def send_once(self) -> bool:
        job = await asyncio.to_thread(self.store.claim, "ready", time.time(), self.scope)
        if not job:
            return False
        try:
            parts = await asyncio.to_thread(self.store.parts, job)
            part = next((part for part in parts if part["state"] != "sent"), None)
            if part:
                if part["state"] != "pending":
                    raise BrowserUnavailable("uncertain_delivery_part")
                next_send = float(await asyncio.to_thread(self.store.get_meta, "next_send_at", "0"))
                started = time.monotonic()
                await self.delay(max(0, next_send - time.time()))
                if self.stopped.is_set() or await self.paused():
                    await asyncio.to_thread(self.store.finish, job, "ready", time.time())
                    return True
                async with self.ui:
                    await asyncio.to_thread(self.store.measure, job, "dispatch_wait", time.monotonic() - started)
                    await self._send_part(job, part)
            remaining = await asyncio.to_thread(self.store.parts, job)
            state = "ready" if any(part["state"] != "sent" for part in remaining) else "sent"
            await asyncio.to_thread(self.store.finish, job, state, time.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            parts = await asyncio.to_thread(self.store.parts, job)
            uncertain = any(part["state"] == "sending" for part in parts)
            await asyncio.to_thread(self.store.finish, job, "uncertain" if uncertain else "ready", time.time())
            log.warning("inbox delivery stopped: job=%s reason=%s", job["id"][:12], type(exc).__name__)
            await self.pause("delivery_requires_review" if uncertain else "browser_requires_attention")
        return True

    async def send_loop(self) -> None:
        while not self.stopped.is_set():
            if await self.paused() or not await self.send_once():
                await self.delay(1)

    async def maintenance_loop(self) -> None:
        while not self.stopped.is_set():
            await asyncio.to_thread(self.store.expire_choices, time.time())
            await self.processor.cleanup(time.time())
            await asyncio.to_thread(self.store.set_meta, "heartbeat", str(time.time()))
            await self.delay(60)

    async def run(self) -> None:
        pipeline = NativePipeline(self) if self.send_enabled and self.settings.native_pipeline else None
        if pipeline:
            await pipeline.install()
        await asyncio.to_thread(self.store.set_meta, "outbound_mode", "native" if pipeline else "legacy")
        loops = [self.collect_loop(), self.maintenance_loop()]
        if self.events:
            loops.append(self.event_loop())
        if pipeline:
            loops += [pipeline.plan_loop(), pipeline.dispatch_loop(), pipeline.observe_loop()]
            loops += [pipeline.upload_loop() for _ in range(self.settings.upload_workers)]
        elif self.send_enabled:
            loops.append(self.send_loop())
        if self.send_enabled:
            loops += [self.prepare_loop() for _ in range(self.settings.workers)]
        tasks = [asyncio.create_task(loop) for loop in loops]
        stop = asyncio.create_task(self.stopped.wait())
        try:
            done, _ = await asyncio.wait([*tasks, stop], return_when=asyncio.FIRST_COMPLETED)
            for task in done - {stop}:
                task.result()
                raise RuntimeError("账号工作流意外停止")
        finally:
            for task in [*tasks, stop]:
                task.cancel()
            await asyncio.gather(*tasks, stop, return_exceptions=True)
            if pipeline:
                await pipeline.close()
