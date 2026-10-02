"""使用统计：什么时候有多少人在用、用的哪个平台、成功率多少。

SQLite 落在 data/stats.db，站长开 /stats 看（需要 PARSE_VIDEO_STATS_TOKEN）。
只记事件不记链接，IP 用签名密钥做 HMAC 后只留 12 位——能数出"多少个人"，还原不出是谁。
（解析失败的链接另存 7 天，见 failures.py。）
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import hmac
import ipaddress
import os
import threading
import time

from . import db
from .convert import config, net

DB_PATH = config.DATA_DIR / "stats.db"
TOKEN = os.environ.get("PARSE_VIDEO_STATS_TOKEN", "").strip()
RETENTION_DAYS = int(os.environ.get("PARSE_VIDEO_STATS_DAYS", 90))

KINDS = ("view", "parse", "job", "download")

# 站长自己和服务器自己的请求不计入：本机 / 内网地址（部署脚本冒烟、容器里的测试、docker 网关），
# 加上这里列出的 IP（服务器自己的公网地址：容器里的浏览器测试绕公网回来就是它）
IGNORE_IPS = {ip.strip() for ip in os.environ.get("PARSE_VIDEO_STATS_IGNORE_IPS", "").split(",") if ip.strip()}
TEST_COOKIE = "sz_test"  # /test 用统计口令开启，签名值，httponly
TEST_FLAG_COOKIE = "sz_t"  # 给页面上的「测试模式」小标记看的，不参与判断

# 这次请求不计入统计。中间件里设；请求里 create_task 出去的转换任务会继承，任务做完记 job 时也就跳过了
_muted: contextvars.ContextVar[bool] = contextvars.ContextVar("stats_muted", default=False)


def mute() -> None:
    _muted.set(True)


def ignored_ip(ip: str) -> bool:
    if ip in IGNORE_IPS:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def test_cookie_value() -> str:
    return hmac.new(net.secret_key(), b"stats-test-device", hashlib.sha256).hexdigest()[:32]


def is_test_cookie(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value, test_cookie_value())


_buf: list[tuple] = []
_lock = threading.Lock()


def enabled() -> bool:
    return bool(TOKEN)


def hash_ip(ip: str) -> str:
    """IP 的 12 位 HMAC 摘要：能数出「多少个人」，还原不出是谁。"""
    return hmac.new(net.secret_key(), (ip or "").encode(), hashlib.sha256).hexdigest()[:12]


_SCHEMA = (
    "PRAGMA journal_mode=WAL;"
    "CREATE TABLE IF NOT EXISTS events ("
    " ts REAL NOT NULL, kind TEXT NOT NULL, ip TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',"
    " ok INTEGER NOT NULL DEFAULT 1, reason TEXT NOT NULL DEFAULT '', ms INTEGER NOT NULL DEFAULT 0);"
    "CREATE INDEX IF NOT EXISTS events_ts ON events (ts);"
)


def _db():
    return db.transaction(DB_PATH, _SCHEMA)


def counted(ip: str) -> bool:
    """这个请求算不算真实用户：站长测试设备（/test）和服务器自己的请求不算。"""
    return not _muted.get() and not ignored_ip(ip)


def record(kind: str, ip: str, *, source: str = "", ok: bool = True, reason: str = "", ms: float = 0) -> None:
    """先攒在内存里，flusher 每几秒批量落盘；请求路径上不碰磁盘。没配 token 就什么都不记。"""
    if not TOKEN or kind not in KINDS or not counted(ip):
        return
    row = (time.time(), kind, hash_ip(ip), (source or "")[:32], int(bool(ok)), (reason or "")[:32], int(ms))
    with _lock:
        _buf.append(row)


# 同一个人同一个文件，这么久之内只算一次下载
DOWNLOAD_DEDUPE_SECONDS = 600
_recent_downloads: dict[tuple[str, str], float] = {}


def record_download(ip: str, url: str, *, source: str = "") -> None:
    """下载器（IDM 之类）会把一个文件切成几十段并发拉，断了还重试，每段都记的话
    1 次下载能记成 30 次（2026-10-02 一个 1GB 的抖音视频就是这样）。这里按 IP + 地址去重。"""
    now = time.monotonic()
    with _lock:
        if now - _recent_downloads.get((ip, url), -DOWNLOAD_DEDUPE_SECONDS) < DOWNLOAD_DEDUPE_SECONDS:
            return
        if len(_recent_downloads) > 1000:  # 顺手清掉过期的，别越攒越多
            for key in [k for k, t in _recent_downloads.items() if now - t >= DOWNLOAD_DEDUPE_SECONDS]:
                del _recent_downloads[key]
        _recent_downloads[(ip, url)] = now
    record("download", ip, source=source)


def flush() -> int:
    with _lock:
        rows, _buf[:] = list(_buf), []
    if not rows:
        return 0
    with _db() as conn:
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


async def flusher(interval: float = 5.0) -> None:
    try:
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):  # 统计写失败不能影响服务
                await asyncio.to_thread(flush)
    finally:
        # 进程退出前把攒着的写掉
        with contextlib.suppress(Exception):
            flush()


def prune() -> int:
    cutoff = time.time() - RETENTION_DAYS * 86400
    with _db() as conn:
        return conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount


_COUNTERS = ("view", "parse", "parse_ok", "job", "job_ok", "download")


def _empty_bucket(t: float) -> dict:
    return {"t": t, **dict.fromkeys(_COUNTERS, 0), "users": 0}


def _series(rows: list, users: dict, since: float, until: float, step: int, tz_offset: int) -> list[dict]:
    """查询结果摊到连续的时间格子上，没有数据的格子补 0。"""
    buckets: dict[float, dict] = {}
    t = since - ((since + tz_offset) % step)
    while t < until:
        buckets[t] = _empty_bucket(t)
        t += step
    for b, kind, n, ok in rows:
        cell = buckets.setdefault(b, _empty_bucket(b))
        cell[kind] = n
        if kind in ("parse", "job"):
            cell[kind + "_ok"] = ok or 0
    for b, n in users.items():
        if b in buckets:
            buckets[b]["users"] = n
    return [buckets[k] for k in sorted(buckets)]


def summary(since: float, until: float, step: int, tz_offset: int = 0) -> dict:
    """[since, until) 内按 step 秒分桶。tz_offset 是客户端时区偏移（秒），让"按天"的桶从当地零点开始。

    每个桶：浏览 / 解析（成功数）/ 任务（成功数）/ 下载 / 用了解析或任务的人数（去重 IP）。
    """
    step = max(60, int(step))
    bucket = f"(CAST((ts + {tz_offset}) / {step} AS INTEGER) * {step} - {tz_offset})"
    with _db() as conn:
        # 解析结果缓存命中不是一次新的解析：一个人连点几下会把同一个结果重放好几遍，
        # 计进去会虚增次数、压低成功率。口径跟下面的 by_source 保持一致。
        rows = conn.execute(
            f"SELECT {bucket} AS b, kind, COUNT(*), SUM(ok) FROM events "
            "WHERE ts >= ? AND ts < ? AND NOT (kind = 'parse' AND reason = 'cache') "
            "GROUP BY b, kind",
            (since, until),
        ).fetchall()
        users = dict(
            conn.execute(
                f"SELECT {bucket} AS b, COUNT(DISTINCT ip) FROM events "
                "WHERE ts >= ? AND ts < ? AND kind IN ('parse', 'job') GROUP BY b",
                (since, until),
            ).fetchall()
        )
        (total_users,) = conn.execute(
            "SELECT COUNT(DISTINCT ip) FROM events WHERE ts >= ? AND ts < ? AND kind IN ('parse', 'job')",
            (since, until),
        ).fetchone()
        by_source = conn.execute(
            "SELECT source, COUNT(*), SUM(ok), COUNT(DISTINCT ip), CAST(AVG(ms) AS INTEGER) FROM events "
            "WHERE ts >= ? AND ts < ? AND kind = 'parse' AND reason != 'cache' "
            "GROUP BY source ORDER BY 2 DESC LIMIT 20",
            (since, until),
        ).fetchall()
        reasons = conn.execute(
            "SELECT reason, COUNT(*) FROM events WHERE ts >= ? AND ts < ? AND kind = 'parse' AND ok = 0 "
            "AND reason != 'cache' GROUP BY reason ORDER BY 2 DESC LIMIT 8",
            (since, until),
        ).fetchall()
        jobs = conn.execute(
            "SELECT source, COUNT(*), SUM(ok), CAST(AVG(ms) AS INTEGER) FROM events "
            "WHERE ts >= ? AND ts < ? AND kind = 'job' GROUP BY source ORDER BY 2 DESC",
            (since, until),
        ).fetchall()
        (first,) = conn.execute("SELECT MIN(ts) FROM events").fetchone()

    series = _series(rows, users, since, until, step, tz_offset)
    totals = {k: sum(c[k] for c in series) for k in _COUNTERS}
    totals["users"] = total_users or 0
    return {
        "since": since,
        "until": until,
        "step": step,
        "first": first,
        "totals": totals,
        "series": series,
        "sources": [{"source": s, "n": n, "ok": ok or 0, "users": u, "ms": ms or 0} for s, n, ok, u, ms in by_source],
        "reasons": [{"reason": r, "n": n} for r, n in reasons],
        "jobs": [{"type": s, "n": n, "ok": ok or 0, "ms": ms or 0} for s, n, ok, ms in jobs],
    }


def check_token(token: str | None) -> bool:
    return enabled() and bool(token) and hmac.compare_digest(token, TOKEN)
