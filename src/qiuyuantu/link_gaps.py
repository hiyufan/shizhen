"""把最近解析失败的链接按「站点 + 路径样式」归组，找出还不认识的链接格式。

平台常会新增链接样式（Steam 商店页、Pixiv 镜像、短链落到的新路径……），单看 /stats 上一条条失败
不容易发现同一种样式在反复失败。这里只读 failures.db，把路径里的数字和长 ID 换成占位符后计数，
并标出按当前代码这种链接会交给哪个解析器，方便判断是缺格式还是平台本身拿不到。

只输出路径样式和参数名，不输出参数值。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 「不支持」和「页面结构变了」最可能是缺格式；删除、登录、风控等是内容本身的原因
DEFAULT_REASONS = ("unsupported", "parse")


def _segment(part: str) -> str:
    if part.isdecimal():
        return "{n}"
    if len(part) >= 8 and re.search(r"[0-9]", part) and re.fullmatch(r"[A-Za-z0-9_:.-]+", part):
        return "{id}"
    return part[:24]


def link_shape(link: str) -> tuple[str, str]:
    """(主机, 路径样式)：/app/730/CS2 → /app/{n}/CS2，查询参数只留名字。"""
    try:
        parsed = urlparse(link.strip())
    except ValueError:
        return "", ""
    host = (parsed.hostname or "").lower().removeprefix("www.")
    raw = parsed.path.split("/")
    parts = [_segment(part) for part in raw]
    last = max((i for i, part in enumerate(parts) if part), default=0)
    if last and parts[last - 1] in {"{n}", "{id}"} and re.search(r"[-_%]", raw[last]):
        # ID 后面的标题段（/app/730/Counter_Strike_2）各不相同，归成一类
        parts[last] = "{slug}"
    path = "/".join(parts)
    keys = sorted(parse_qs(parsed.query, keep_blank_values=True))[:6]
    return host, path + ("?" + "&".join(keys) if keys else "")


def _now_routed(link: str) -> str:
    from .parser import detect_source

    try:
        return detect_source(link).value
    except Exception:  # noqa: BLE001 - 报告工具，单条链接异常不影响其余
        return "?"


def gaps(db_path: Path, days: float = 7, reasons: tuple[str, ...] = DEFAULT_REASONS) -> list[dict]:
    if not db_path.exists():
        return []
    marks = ",".join("?" * len(reasons))
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            f"SELECT ts, platform, reason, msg, link FROM failures WHERE ts >= ? AND reason IN ({marks}) ORDER BY ts",
            (time.time() - days * 86400, *reasons),
        ).fetchall()
    groups: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "links": set()})
    for _ts, platform, reason, msg, link in rows:
        host, shape = link_shape(link)
        group = groups[(host, shape, platform, reason)]
        group["count"] += 1
        group["links"].add(link)
        group["msg"] = msg
        group["example"] = link
    report = [
        {
            "host": host,
            "shape": shape,
            "platform": platform,
            "reason": reason,
            "count": group["count"],
            "links": len(group["links"]),
            "now": _now_routed(group["example"]),
            # 原因里可能引用带令牌的地址，参数值一并去掉
            "msg": re.sub(r"\?[^\s，,)）]*", "?…", group["msg"])[:120],
        }
        for (host, shape, platform, reason), group in groups.items()
    ]
    return sorted(report, key=lambda row: (-row["links"], -row["count"], row["host"]))


def main(argv: list[str] | None = None) -> None:
    from .failures import DB_PATH

    args = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    args.add_argument("--db", type=Path, default=DB_PATH, help="failures.db 路径（只读打开）")
    args.add_argument("--days", type=float, default=7)
    args.add_argument("--reason", action="append", help="只看这些失败原因，可重复；默认 unsupported 和 parse")
    args.add_argument("--json", action="store_true")
    opts = args.parse_args(argv)
    report = gaps(opts.db, opts.days, tuple(opts.reason or DEFAULT_REASONS))
    if opts.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for row in report:
        moved = f" → 现在交给 {row['now']}" if row["now"] != row["platform"] else ""
        where = f"[{row['platform']}:{row['reason']}]{moved}"
        print(f"{row['links']:>3} 条 / {row['count']:>3} 次  {row['host']}{row['shape']}  {where}")
        print(f"      {row['msg']}")
