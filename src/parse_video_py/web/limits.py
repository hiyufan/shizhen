"""公网部署用的限流：按客户端 IP 的令牌桶 + 并发数上限，外加几个全站总量的上限。

单进程内存实现，够一台机器用；多机部署时每台各自限流。
IPv6 按 /64 计：一台机器通常分到一整段 /64，按单个地址算的话每个请求换个地址就绕过去了。
"""

from __future__ import annotations

import ipaddress
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


def limit_key(ip: str) -> str:
    """限流用的键：IPv4 就是地址本身，IPv6 取所在的 /64。统计里数人数仍按完整地址。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 4:
        return str(addr)
    if addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


GLOBAL = "*"  # 全站共用一个桶 / 计数时的键


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

    def hit(self, ip: str, message: str = "请求太频繁了，{wait} 秒后再试") -> None:
        """ip 传 GLOBAL 就是全站总量。"""
        self._sweep()
        key = ip if ip == GLOBAL else limit_key(ip)
        now = time.time()
        b = self._buckets.get(key)
        if b is None:
            # updated 用同一个 now：晚取一点的话补令牌算出负数，上限为 1 时第一次就被拒
            b = self._buckets[key] = _Bucket(tokens=float(self.burst), updated=now)
        b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.burst / self.per)
        b.updated = now
        if b.tokens < 1:
            wait = int((1 - b.tokens) * self.per / self.burst) + 1
            raise HTTPException(429, message.format(wait=wait), headers={"Retry-After": str(wait)})
        b.tokens -= 1

    async def __call__(self, request: Request) -> str:
        ip = client_ip(request)
        self.hit(ip)
        return ip


class Concurrency:
    """同时进行的数量上限（代理流、后台任务）：按 IP，或传 GLOBAL 算全站。"""

    def __init__(self, name: str, limit: int, busy: str = ""):
        self.name, self.limit = name, limit
        self._busy = busy or f"你有 {limit} 个{name}还在进行，等一下再来"
        self._active: dict[str, int] = defaultdict(int)

    def acquire(self, ip: str) -> None:
        key = ip if ip == GLOBAL else limit_key(ip)
        if self._active[key] >= self.limit:
            raise HTTPException(429, self._busy)
        self._active[key] += 1

    def release(self, ip: str) -> None:
        key = ip if ip == GLOBAL else limit_key(ip)
        self._active[key] = max(0, self._active[key] - 1)
        if self._active[key] == 0:
            self._active.pop(key, None)


_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp", ".avif")
# 各家图片 CDN 的处理参数, 带上就肯定是图片而不是视频
_IMAGE_HINT = ("imageview2", "image_process", "x-oss-process", "format/jpg", "format/webp", "/format/png")


def looks_like_image(url: str) -> bool:
    """图文笔记一次十几张图, 让它们跟视频抢同一个并发池的话用户自己就把自己限流了。

    这里只按 URL 猜, 猜错的代价很小: 当成图片就少一层并发保护(令牌桶还在),
    当成视频最多是并发额度紧一点。
    """
    path, _, query = url.lower().partition("?")
    return path.endswith(_IMAGE_EXT) or any(h in query for h in _IMAGE_HINT)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# 默认值按"一台 4 核 VPS、几百个日活"设的，可用环境变量覆盖
parse_limit = RateLimit("parse", _env_int("PARSE_VIDEO_RL_PARSE", 10), 60)  # 每分钟 10 次解析
# 全站每分钟真正去平台抓取的次数（缓存命中、不支持的链接不算）。挡的是换着 IP 刷：
# 一直打到抖音 / YouTube 的话，服务器 IP 在平台那边会被风控，所有用户一起解析失败
parse_upstream_global = RateLimit("parse-global", _env_int("PARSE_VIDEO_RL_PARSE_GLOBAL", 120), 60)
job_limit = RateLimit("job", _env_int("PARSE_VIDEO_RL_JOB", 20), 600)  # 每 10 分钟 20 个任务
upload_limit = RateLimit("upload", _env_int("PARSE_VIDEO_RL_UPLOAD", 10), 3600)  # 每小时 10 次上传
feedback_limit = RateLimit("feedback", _env_int("PARSE_VIDEO_RL_FEEDBACK", 5), 3600)  # 每小时 5 次反馈
# 每分钟 240 次代理请求（含 Range 分段、图文笔记的十几张图）
proxy_limit = RateLimit("proxy", _env_int("PARSE_VIDEO_RL_PROXY", 240), 60)
proxy_streams = Concurrency("下载", _env_int("PARSE_VIDEO_MAX_STREAMS_PER_IP", 4))
# 全站同时转发的视频流：每路一直占着出口带宽，换着 IP 开流的话按 IP 的上限管不住
proxy_streams_global = Concurrency(
    "下载", _env_int("PARSE_VIDEO_MAX_STREAMS", 64), busy="现在下载的人太多了，稍等一下再试"
)
jobs_per_ip = Concurrency("任务", _env_int("PARSE_VIDEO_MAX_JOBS_PER_IP", 2))
