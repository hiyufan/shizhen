"""解析失败的链接：留 7 天给站长排查，在 /stats 页面上看最近的。成功的解析不记链接。

以前失败链接只写在容器日志里，每次部署重建容器就没了，事后只知道「快手不支持 4 次」，
不知道是哪几条链接。站长自己的测试设备和服务器自己的请求不记（和统计同一个口径）。
"""

from __future__ import annotations

import time

from . import db
from .convert import config

DB_PATH = config.DATA_DIR / "failures.db"
RETENTION_DAYS = 7

_SCHEMA = (
    "PRAGMA journal_mode=WAL;"
    "CREATE TABLE IF NOT EXISTS failures ("
    " ts REAL NOT NULL, platform TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',"
    " msg TEXT NOT NULL DEFAULT '', link TEXT NOT NULL);"
    "CREATE INDEX IF NOT EXISTS failures_ts ON failures (ts);"
)


def _db():
    return db.transaction(DB_PATH, _SCHEMA, rows=True)


def record(link: str, platform: str, reason: str, msg: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO failures (ts, platform, reason, msg, link) VALUES (?, ?, ?, ?, ?)",
            (time.time(), platform, reason, msg[:300], link[:2000]),
        )


def recent(since: float, limit: int = 100) -> list[dict]:
    """since 之后的失败，同一条链接只留最近一次并带上次数，新的在前。"""
    if not DB_PATH.exists():
        return []
    with _db() as conn:
        rows = conn.execute(
            "SELECT MAX(ts) AS ts, platform, reason, msg, link, COUNT(*) AS n FROM failures"
            " WHERE ts >= ? GROUP BY link ORDER BY ts DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def prune() -> int:
    if not DB_PATH.exists():
        return 0
    with _db() as conn:
        return conn.execute("DELETE FROM failures WHERE ts < ?", (time.time() - RETENTION_DAYS * 86400,)).rowcount
