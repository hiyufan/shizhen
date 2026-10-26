"""等待视频 / 实况选择时预先准备原格式，选择后只剩提交与回执。

实际选择多数包含原格式（原视频 / LIVE 或都要），所以在用户思考期间把原格式下载进共享缓存，
并在页面内预上传一次。页面资源缓存按账号与文件内容摘要索引，选择后的正式上传同内容直接复用，
不会重复上传。预热不创建平台消息、不改任务状态，失败只放弃预热，正式流程照常从头准备。
有正式准备或上传排队时预热让路，每次只预热一条，避免占用正式任务的下载和上传路数。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from .media import Asset, delivery_parts
from .models import Message
from .outbound import individual_parts

log = logging.getLogger(__name__)


class ChoiceWarmup:
    def __init__(self, pipeline):
        self.pipeline, self.engine = pipeline, pipeline.engine
        self.store, self.settings = pipeline.store, pipeline.settings
        # 每条待选只尝试一次；重启后页面缓存可能还在，再试一次的代价也只是一次缓存命中。
        self.attempted: set[str] = set()

    async def idle(self) -> bool:
        if await self.engine.paused():
            return False
        return not await asyncio.to_thread(self.store.real_work_pending)

    async def loop(self) -> None:
        while not self.engine.stopped.is_set():
            if await self.idle():
                await self.once()
            await self.engine.delay(1)

    async def once(self) -> bool:
        for row in await asyncio.to_thread(self.store.warm_candidates, time.time()):
            if row["job"] in self.attempted or (self.engine.scope and row["conversation"] not in self.engine.scope):
                continue
            self.attempted.add(row["job"])
            started = time.monotonic()
            try:
                manifest = await self.source(row)
                if manifest:
                    await self.upload(row, manifest)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("inbox choice warmup skipped: job=%s reason=%s", row["job"][:12], type(exc).__name__)
            await self.measure(row, "choice_warmup", time.monotonic() - started)
            return True
        return False

    async def source(self, row: dict) -> dict | None:
        manifest = json.loads(row["manifest"])
        # 相册实况的原格式是原生转发，没有可预传的文件；GIF 要选择后才从页面取视频。
        if manifest.get("native_live"):
            return None
        if not manifest.get("metadata_only"):
            return manifest
        message = Message(**json.loads(row["payload"]))
        assets = [Asset(**asset) for asset in manifest["assets"]]
        # 与选择后准备原格式用同一个缓存键；正式准备同时开始时由处理器的同键锁合并为一次下载。
        started = time.monotonic()
        key, prepared = await asyncio.wait_for(
            self.engine.processor.prepare(message, assets), timeout=self.settings.job_timeout
        )
        await self.measure(row, "choice_warmup_download", time.monotonic() - started)
        await asyncio.to_thread(self.store.warm_choice_source, row["job"], row["manifest"], key, prepared)
        return prepared

    async def upload(self, row: dict, manifest: dict) -> None:
        # 分开记录整段预传与页面上传器实际传输的秒数，差值是摘要、文件交接和等待上传路数。
        for part in individual_parts(delivery_parts(manifest, self.settings.photo_batch)):
            if self.engine.stopped.is_set() or not await self.idle():
                return
            files = await self.pipeline.files(part)
            if sum(file.stat().st_size for file in files) > self.settings.upload_bytes:
                return
            started = time.monotonic()
            result = await asyncio.wait_for(
                self.pipeline.bridge.warm(part, files), timeout=self.settings.upload_timeout
            )
            await self.measure(row, "choice_warmup_upload", time.monotonic() - started)
            await self.measure(row, "choice_warmup_stream", result.get("upload_seconds", 0))

    async def measure(self, row: dict, phase: str, seconds: float) -> None:
        await asyncio.to_thread(self.store.measure, {"id": row["job"]}, phase, seconds)
