"""后台任务结束后必须还配额、记统计。"""

import asyncio

from parse_video_py.convert import jobs, limits


def test_finished_jobs_release_quota_and_record(monkeypatch):
    recorded = []
    monkeypatch.setattr(jobs.usage_stats, "record", lambda kind, ip, **kw: recorded.append((kind, kw["ok"])))
    quota = limits.Concurrency("任务", 2)

    async def ok(job):
        pass

    async def boom(job):
        raise RuntimeError("x")

    async def run():
        # 超过配额数量的任务依次跑完，每个都能提交：之前名额只占不还，第 3 个起一直 429
        for fn in (ok, boom, ok, ok):
            quota.acquire("1.2.3.4")
            job = jobs.start("download", fn, on_release=lambda: quota.release("1.2.3.4"))
            await job.task

    asyncio.run(run())
    assert recorded == [("job", True), ("job", False), ("job", True), ("job", True)]
