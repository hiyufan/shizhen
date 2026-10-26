"""合并通知不丢失检查 / 等待竞态，多 worker 都能醒，阶段落盘后立即推进。"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.engine import Engine
from qiuyuantu_inbox.models import Settings
from qiuyuantu_inbox.pipeline import NativePipeline
from qiuyuantu_inbox.store import Store
from qiuyuantu_inbox.wakeups import Wakeups


async def test_notification_between_queue_check_and_wait_is_not_lost():
    wakes = Wakeups()
    version = wakes.version("prepare")
    wakes.notify("prepare")
    await asyncio.wait_for(wakes.wait("prepare", version, 60), 1)
    assert not wakes.waiters["prepare"]


async def test_burst_wakes_all_workers_and_cancellation_leaves_no_waiter():
    wakes = Wakeups()
    tasks = [asyncio.create_task(wakes.wait("upload", 0, 60)) for _ in range(2)]
    await asyncio.sleep(0)
    for _ in range(1000):
        wakes.notify("upload")
    await asyncio.wait_for(asyncio.gather(*tasks), 1)
    assert not wakes.waiters["upload"]
    task = asyncio.create_task(wakes.wait("upload", wakes.version("upload"), 60))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not wakes.waiters["upload"]


async def test_timeout_still_checks_queue_when_notification_is_missing():
    wakes = Wakeups()
    await asyncio.wait_for(wakes.wait("events", 0, 0.01), 1)
    assert wakes.version("events") == 0 and not wakes.waiters["events"]


async def test_shutdown_interrupts_stage_waiters_without_waiting_for_scan_interval(tmp_path):
    store = Store(tmp_path, Settings())
    browser = SimpleNamespace(conversations=AsyncMock(return_value=[]))
    engine = Engine(store, browser, set(), False)
    task = asyncio.create_task(engine.run())

    async def waiting():
        while not engine.wakes.waiters["collect"]:
            await asyncio.sleep(0.001)

    try:
        await asyncio.wait_for(waiting(), 1)
        engine.stopped.set()
        await asyncio.wait_for(task, 1)
        assert not engine.wakes.waiters["collect"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    ("requested", "failure", "expected"),
    [(True, False, None), (False, False, RuntimeError), (True, True, ValueError)],
)
async def test_worker_finishing_before_stop_observer_preserves_stop_and_failure_semantics(
    tmp_path, requested, failure, expected
):
    engine = Engine(Store(tmp_path, Settings()), SimpleNamespace(), set(), False)

    async def delayed_observer():
        # 让停止观察者保持 pending，稳定复现工作循环先结束的调度顺序。
        await asyncio.Future()

    async def worker():
        if requested:
            engine.stopped.set()
        if failure:
            raise ValueError("worker_failed")

    engine.stopped.wait = delayed_observer
    engine.collect_loop = worker
    engine.maintenance_loop = delayed_observer
    if expected:
        with pytest.raises(expected):
            await asyncio.wait_for(engine.run(), 1)
    else:
        await asyncio.wait_for(engine.run(), 1)


async def test_incoming_hints_cannot_bypass_event_adapter_error_backoff(tmp_path):
    engine = Engine(Store(tmp_path, Settings()), SimpleNamespace(), set(), False)
    engine.event_once = AsyncMock(side_effect=RuntimeError())
    backing_off = asyncio.Event()

    async def delay(seconds):
        assert seconds == 2
        backing_off.set()
        await asyncio.Future()

    engine.delay = delay
    task = asyncio.create_task(engine.event_loop())
    try:
        await asyncio.wait_for(backing_off.wait(), 1)
        for _ in range(1000):
            engine.wakes.notify("events")
        await asyncio.sleep(0)
        assert engine.event_once.await_count == 1
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_buffered_batches_continue_immediately_after_one_coalesced_hint(tmp_path):
    engine = Engine(Store(tmp_path, Settings()), SimpleNamespace(), set(), False)
    row = {"sequence": 1, "row": {"id": "buffered"}}
    batches = [
        {"generation": "g", "gaps": 0, "rows": [row] * 100, "more": True},
        {"generation": "g", "gaps": 0, "rows": [row], "more": False},
    ]
    engine.events = SimpleNamespace(read=AsyncMock(side_effect=batches), ack=AsyncMock())
    engine.capture_rows = AsyncMock()
    version = engine.wakes.version("events")
    await engine.event_once()
    await asyncio.wait_for(engine.wakes.wait("events", version, 60), 1)
    await engine.event_once()
    assert [len(call.args[0]) for call in engine.capture_rows.await_args_list] == [100, 1]
    assert engine.events.ack.await_count == 2


async def test_receive_prepare_plan_and_submit_advance_without_poll_timeout(tmp_path):
    store = Store(tmp_path, Settings(native_pipeline=True))
    store.bind("account", 1000)
    browser = SimpleNamespace(settings=store.settings, open=AsyncMock(), guard=AsyncMock())
    engine = Engine(store, browser, set(), True)
    engine.prepare_message = AsyncMock(return_value=("", [{"text": "result"}]))
    batch = {"generation": "g", "gaps": 0, "rows": []}

    async def ack(_batch):
        batch["rows"] = []

    engine.events = SimpleNamespace(read=AsyncMock(side_effect=lambda: batch.copy()), ack=ack)
    pipeline = NativePipeline(engine)
    submitted = asyncio.Event()
    pipeline.bridge = SimpleNamespace(submit=AsyncMock(side_effect=lambda *args: submitted.set()))
    stages = {stage: asyncio.Event() for stage in ("events", "prepare", "plan", "dispatch")}
    original = engine.wakes.wait

    async def wait(stage, version, seconds):
        stages[stage].set()
        # 本测试禁止依赖定时补查；生产仍用各阶段原有的有界超时。
        await original(stage, version, 60)

    engine.wakes.wait = wait
    tasks = [
        asyncio.create_task(loop())
        for loop in (engine.event_loop, engine.prepare_loop, pipeline.plan_loop, pipeline.dispatch_loop)
    ]
    try:
        await asyncio.wait_for(asyncio.gather(*(stage.wait() for stage in stages.values())), 2)
        batch["rows"] = [
            {
                "sequence": 1,
                "row": {
                    "id": "new",
                    "conversation": "chat",
                    "sender": "sender",
                    "created": time.time(),
                    "kind": "work",
                    "data": {"work_id": "123"},
                },
            }
        ]
        engine.wakes.notify("events")
        await asyncio.wait_for(submitted.wait(), 2)
        assert not store.inbound()
        assert store.list_jobs()[0]["state"] == "sending"
        assert pipeline.bridge.submit.await_count == 1
        assert store.parts(store.list_jobs()[0])[0]["state"] == "sending"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
