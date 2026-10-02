"""本地 SQLite 的公用写法：统计、反馈、失败链接各一个库文件，都在 data/ 下。"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .convert import config


@contextlib.contextmanager
def transaction(path: Path, schema: str, *, rows: bool = False) -> Iterator[sqlite3.Connection]:
    """打开库（表不存在就按 schema 建）跑一次事务：成功提交、出错回滚，用完关掉连接。

    注意别写成 `with sqlite3.connect() as conn`：那只管事务，不关连接，要等垃圾回收才释放。
    rows=True 时按列名取值（sqlite3.Row）。
    """
    config.ensure_dirs()
    conn = sqlite3.connect(path, timeout=10)
    if rows:
        conn.row_factory = sqlite3.Row
    try:
        conn.executescript(schema)
        with conn:
            yield conn
    finally:
        conn.close()
