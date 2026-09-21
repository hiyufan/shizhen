"""使用统计：什么时候有多少人在用、用的哪个平台、成功率多少。

SQLite 落在 data/stats.db，站长开 /stats 看（需要 PARSE_VIDEO_STATS_TOKEN）。
只记事件不记内容：链接不存，IP 用签名密钥做 HMAC 后只留 12 位——能数出"多少个人"，还原不出是谁。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import sqlite3
import threading
import time
from typing import Optional

from .convert import config, net

DB_PATH = config.DATA_DIR / "stats.db"
TOKEN = os.environ.get("PARSE_VIDEO_STATS_TOKEN", "").strip()
RETENTION_DAYS = int(os.environ.get("PARSE_VIDEO_STATS_DAYS", 90))

KINDS = ("view", "parse", "job", "download")

_buf: list[tuple] = []
_lock = threading.Lock()
_ready = False


def enabled() -> bool:
    return bool(TOKEN)


def _hash_ip(ip: str) -> str:
    return hmac.new(net._secret(), (ip or "").encode(), hashlib.sha256).hexdigest()[:12]


def _connect() -> sqlite3.Connection:
    global _ready
    config.ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    if not _ready:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "ts REAL NOT NULL, kind TEXT NOT NULL, ip TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', "
            "ok INTEGER NOT NULL DEFAULT 1, reason TEXT NOT NULL DEFAULT '', ms INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
        conn.commit()
        _ready = True
    return conn


def record(kind: str, ip: str, *, source: str = "", ok: bool = True, reason: str = "", ms: float = 0) -> None:
    """先攒在内存里，flusher 每几秒批量落盘；请求路径上不碰磁盘。没配 token 就什么都不记。"""
    if not TOKEN or kind not in KINDS:
        return
    row = (time.time(), kind, _hash_ip(ip), (source or "")[:32], int(bool(ok)), (reason or "")[:32], int(ms))
    with _lock:
        _buf.append(row)


def flush() -> int:
    with _lock:
        rows, _buf[:] = list(_buf), []
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


async def flusher(interval: float = 5.0) -> None:
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(flush)
            except Exception:  # noqa: BLE001 - 统计写失败不能影响服务
                pass
    finally:
        # 进程退出前把攒着的写掉
        try:
            flush()
        except Exception:  # noqa: BLE001
            pass


def prune() -> int:
    cutoff = time.time() - RETENTION_DAYS * 86400
    with _connect() as conn:
        n = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
    return n


def summary(since: float, until: float, step: int, tz_offset: int = 0) -> dict:
    """[since, until) 内按 step 秒分桶。tz_offset 是客户端时区偏移（秒），让"按天"的桶从当地零点开始。

    每个桶：浏览 / 解析（成功数）/ 任务（成功数）/ 下载 / 用了解析或任务的人数（去重 IP）。
    """
    step = max(60, int(step))
    bucket = f"(CAST((ts + {tz_offset}) / {step} AS INTEGER) * {step} - {tz_offset})"
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {bucket} AS b, kind, COUNT(*), SUM(ok) FROM events "
            "WHERE ts >= ? AND ts < ? GROUP BY b, kind", (since, until),
        ).fetchall()
        users = dict(conn.execute(
            f"SELECT {bucket} AS b, COUNT(DISTINCT ip) FROM events "
            "WHERE ts >= ? AND ts < ? AND kind IN ('parse', 'job') GROUP BY b", (since, until),
        ).fetchall())
        total_users, = conn.execute(
            "SELECT COUNT(DISTINCT ip) FROM events WHERE ts >= ? AND ts < ? AND kind IN ('parse', 'job')",
            (since, until),
        ).fetchone()
        by_source = conn.execute(
            "SELECT source, COUNT(*), SUM(ok), COUNT(DISTINCT ip), CAST(AVG(ms) AS INTEGER) FROM events "
            "WHERE ts >= ? AND ts < ? AND kind = 'parse' AND reason != 'cache' "
            "GROUP BY source ORDER BY 2 DESC LIMIT 20", (since, until),
        ).fetchall()
        reasons = conn.execute(
            "SELECT reason, COUNT(*) FROM events WHERE ts >= ? AND ts < ? AND kind = 'parse' AND ok = 0 "
            "GROUP BY reason ORDER BY 2 DESC LIMIT 8", (since, until),
        ).fetchall()
        jobs = conn.execute(
            "SELECT source, COUNT(*), SUM(ok), CAST(AVG(ms) AS INTEGER) FROM events "
            "WHERE ts >= ? AND ts < ? AND kind = 'job' GROUP BY source ORDER BY 2 DESC", (since, until),
        ).fetchall()
        first, = conn.execute("SELECT MIN(ts) FROM events").fetchone()

    buckets: dict[float, dict] = {}
    t = since - ((since + tz_offset) % step)
    while t < until:
        buckets[t] = {"t": t, "view": 0, "parse": 0, "parse_ok": 0, "job": 0, "job_ok": 0, "download": 0, "users": 0}
        t += step
    for b, kind, n, ok in rows:
        cell = buckets.setdefault(b, {"t": b, "view": 0, "parse": 0, "parse_ok": 0, "job": 0, "job_ok": 0, "download": 0, "users": 0})
        cell[kind] = n
        if kind in ("parse", "job"):
            cell[kind + "_ok"] = ok or 0
    for b, n in users.items():
        if b in buckets:
            buckets[b]["users"] = n
    series = [buckets[k] for k in sorted(buckets)]
    totals = {k: sum(c[k] for c in series) for k in ("view", "parse", "parse_ok", "job", "job_ok", "download")}
    totals["users"] = total_users or 0
    return {
        "since": since, "until": until, "step": step, "first": first,
        "totals": totals,
        "series": series,
        "sources": [{"source": s, "n": n, "ok": ok or 0, "users": u, "ms": ms or 0} for s, n, ok, u, ms in by_source],
        "reasons": [{"reason": r, "n": n} for r, n in reasons],
        "jobs": [{"type": s, "n": n, "ok": ok or 0, "ms": ms or 0} for s, n, ok, ms in jobs],
    }


def check_token(token: Optional[str]) -> bool:
    return enabled() and bool(token) and hmac.compare_digest(token, TOKEN)
