"""公网部署用的限流：按客户端 IP 的令牌桶 + 并发数上限。

单进程内存实现，够一台机器用；多机部署时每台各自限流。
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

TRUST_PROXY = os.environ.get("PARSE_VIDEO_TRUST_PROXY", "0") == "1"


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        if real := request.headers.get("x-real-ip"):
            return real.strip()
    return request.client.host if request.client else "unknown"


@dataclass
class _Bucket:
    tokens: float
    updated: float = field(default_factory=time.time)


class RateLimit:
    """每 `per` 秒补满 `burst` 个令牌；不够就 429。"""

    def __init__(self, name: str, burst: int, per: float):
        self.name, self.burst, self.per = name, burst, per
        self._buckets: dict[str, _Bucket] = {}
        self._last_sweep = time.time()

    def _sweep(self) -> None:
        now = time.time()
        if now - self._last_sweep < 300:
            return
        self._last_sweep = now
        for ip, b in list(self._buckets.items()):
            if now - b.updated > self.per * 2:
                self._buckets.pop(ip, None)

    def hit(self, ip: str) -> None:
        self._sweep()
        now = time.time()
        b = self._buckets.get(ip)
        if b is None:
            b = self._buckets[ip] = _Bucket(tokens=float(self.burst))
        b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.burst / self.per)
        b.updated = now
        if b.tokens < 1:
            wait = int((1 - b.tokens) * self.per / self.burst) + 1
            raise HTTPException(429, f"请求太频繁了，{wait} 秒后再试", headers={"Retry-After": str(wait)})
        b.tokens -= 1

    async def __call__(self, request: Request) -> str:
        ip = client_ip(request)
        self.hit(ip)
        return ip


class Concurrency:
    """同一 IP 同时进行的数量上限（代理流、后台任务）。"""

    def __init__(self, name: str, limit: int):
        self.name, self.limit = name, limit
        self._active: dict[str, int] = defaultdict(int)

    def acquire(self, ip: str) -> None:
        if self._active[ip] >= self.limit:
            raise HTTPException(429, f"你有 {self.limit} 个{self.name}还在进行，等一下再来")
        self._active[ip] += 1

    def release(self, ip: str) -> None:
        self._active[ip] = max(0, self._active[ip] - 1)
        if self._active[ip] == 0:
            self._active.pop(ip, None)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# 默认值按"一台 4 核 VPS、几百个日活"设的，可用环境变量覆盖
parse_limit = RateLimit("parse", _env_int("PARSE_VIDEO_RL_PARSE", 30), 60)         # 每分钟 30 次解析
job_limit = RateLimit("job", _env_int("PARSE_VIDEO_RL_JOB", 20), 600)             # 每 10 分钟 20 个任务
upload_limit = RateLimit("upload", _env_int("PARSE_VIDEO_RL_UPLOAD", 10), 3600)    # 每小时 10 次上传
proxy_limit = RateLimit("proxy", _env_int("PARSE_VIDEO_RL_PROXY", 120), 60)        # 每分钟 120 次代理请求（含 Range 分段）
proxy_streams = Concurrency("下载", _env_int("PARSE_VIDEO_MAX_STREAMS_PER_IP", 4))
jobs_per_ip = Concurrency("任务", _env_int("PARSE_VIDEO_MAX_JOBS_PER_IP", 2))
