"""各平台解析状态（公开的 /status 页）：每小时拿几条固定链接自检一遍，再配上真实用户近 7 天的成功率。

只靠用户数据不够：快手、B 站一天可能没人用，页面上就是空的，也分不清是平台挂了还是没人来。
自检直接调解析器，不走接口、不进使用统计。结果落在 data/status.db，留 90 天。

自检链接：公开、稳定的放在代码里；小红书、快手这类没有现成公开链接的，用
PARSE_VIDEO_STATUS_PROBES 补（JSON：{"redbook": ["链接", ...]}），只存在服务器上，页面不显示链接。
链接本身失效（作品删了）不算平台出问题：换下一条试，全都失效记成 canary，不计入成功率，日志里提醒站长换。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import statistics
import time
from dataclasses import dataclass

from . import db, stats
from .convert import config
from .parser.errors import ParseError, classify

log = logging.getLogger(__name__)

DB_PATH = config.DATA_DIR / "status.db"
RETENTION_DAYS = 90
INTERVAL = int(os.environ.get("PARSE_VIDEO_STATUS_INTERVAL", 3600))
FIRST_DELAY = 300  # 部署完先让服务稳一会儿，也免得频繁部署时每次都打一轮
PROBE_TIMEOUT = 60
TZ_OFFSET = 8 * 3600  # 页面按北京时间分天
DAYS = 14

_SCHEMA = (
    "PRAGMA journal_mode=WAL;"
    "CREATE TABLE IF NOT EXISTS probes ("
    " ts REAL NOT NULL, platform TEXT NOT NULL, ok INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',"
    " ms INTEGER NOT NULL DEFAULT 0);"
    "CREATE INDEX IF NOT EXISTS probes_ts ON probes (ts);"
)


@dataclass(frozen=True)
class Platform:
    key: str  # 自检记录里的名字
    label: str
    source: str  # 使用统计里的 source；"" = 不单独展示用户数据
    path: str  # 对应的落地页
    note: str  # 这个平台已知的限制，页面上常驻
    probes: tuple[str, ...] = ()


PLATFORMS = (
    Platform(
        "douyin",
        "抖音视频",
        "douyin",
        "/douyin",
        "不用登录，一般 1 秒左右出结果。网页端最高给到 720p。",
        ("https://v.douyin.com/8x0AeP8prW8/",),
    ),
    Platform(
        "douyin-note",
        "抖音图文",
        "",
        "/douyin",
        "要在服务器上打开网页取图，比视频慢，一般 3–5 秒。作者设了登录可见的拿不到。",
        ("https://v.douyin.com/4sip49cAUX8/",),
    ),
    Platform(
        "redbook",
        "小红书",
        "redbook",
        "/xiaohongshu",
        "链接要完整，缺了 xsec_token 会失败。笔记删了或设为私密也会失败。",
    ),
    Platform(
        "kuaishou",
        "快手",
        "kuaishou",
        "/kuaishou",
        "视频和图集都能取；实况只能拿到静态照片，会动的部分快手分享页不给。",
    ),
    Platform(
        "bilibili",
        "B站",
        "bilibili",
        "/bilibili",
        "不登录最高 1080p，720p 以上由服务器合并画面和声音。",
        ("https://www.bilibili.com/video/BV1GJ411x7h7",),
    ),
    Platform(
        "youtube",
        "YouTube",
        "youtube",
        "/youtube",
        "YouTube 会随机要求服务器「确认不是机器人」，碰上了换个时间再试。",
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",),
    ),
)
PLATFORM_BY_KEY = {p.key: p for p in PLATFORMS}

REASON_LABELS = {
    "timeout": "超时",
    "login": "平台要求登录",
    "blocked": "被平台限流",
    "restricted": "平台限制",
    "network": "网络出错",
    "parse": "平台改版",
    "empty": "没拿到内容",
    "unsupported": "不支持",
}

FAQ = [
    (
        "抖音解析不了怎么办？",
        "先看上面抖音那一栏。自检正常，说明平台那边没问题，多半是链接本身：作品删了、设成私密或仅登录可见，"
        "或者复制的不是分享链接。回 App 里点分享、复制链接，整段贴进来再试。自检也失败，就是抖音那边改了东西，"
        "一般几小时到一两天内修好。",
    ),
    (
        "小红书提示解析失败？",
        "最常见的是链接不完整：从浏览器地址栏截的网址缺 xsec_token，会直接失败。在 App 里点分享、复制链接，"
        "整段贴进来。笔记删了或设成私密也拿不到。",
    ),
    (
        "这些数据是怎么来的？",
        "每小时用几条固定的公开链接，把各平台实际解析一遍，记下成功没有、花了多久。「用户解析」是近 7 天真实用户"
        "的解析次数和成功率，作品已删除、贴错链接这类不是平台的问题，不算进去。数据在打开页面时实时算。",
    ),
    (
        "这里显示正常，我的链接还是不行？",
        "解析失败的提示下面有「反馈这个问题」，点一下就会进待修列表，留了邮箱的话，修好会发邮件告诉你。",
    ),
]

# 链接本身的问题，不是平台的问题
_LINK_ROT = ("deleted",)
# 用户这边的原因（贴错链接、作品删了），不算进平台成功率
_USER_SIDE = ("deleted", "unsupported", "cache")


def _db():
    return db.transaction(DB_PATH, _SCHEMA)


def probe_links() -> dict[str, list[str]]:
    links = {p.key: list(p.probes) for p in PLATFORMS}
    raw = os.environ.get("PARSE_VIDEO_STATUS_PROBES", "").strip()
    if raw:
        try:
            extra = json.loads(raw)
        except ValueError:
            log.warning("PARSE_VIDEO_STATUS_PROBES 不是合法 JSON，忽略")
            extra = {}
        for key, value in extra.items():
            if key in links and isinstance(value, list):
                links[key] = [str(v) for v in value if v]
    return links


def record(platform: str, ok: bool, reason: str = "", ms: float = 0, ts: float | None = None) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO probes VALUES (?, ?, ?, ?, ?)",
            (ts or time.time(), platform, int(ok), reason[:32], int(ms)),
        )


async def probe_one(platform: str, links: list[str], parse) -> tuple[bool, str, float]:
    """按顺序试，第一条能给出平台真实状态的为准。返回 (成功, 原因, 毫秒)。"""
    reason, ms = "canary", 0.0
    for link in links:
        started = time.monotonic()
        try:
            await asyncio.wait_for(parse(link), PROBE_TIMEOUT)
            return True, "", (time.monotonic() - started) * 1000
        except asyncio.TimeoutError:
            reason = "timeout"
        except Exception as err:  # noqa: BLE001
            reason = (err if isinstance(err, ParseError) else classify(err)).reason
        ms = (time.monotonic() - started) * 1000
        if reason not in _LINK_ROT:
            return False, reason, ms
        log.warning("状态自检：%s 的自检链接失效了（%s），换一条", platform, link)
        reason = "canary"
    return False, reason, ms


async def run_once(parse) -> None:
    for platform, links in probe_links().items():
        if not links:
            continue
        ok, reason, ms = await probe_one(platform, links, parse)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(record, platform, ok, reason, ms)
        if not ok:
            log.warning("状态自检：%s 失败 reason=%s", platform, reason)


async def loop(parse) -> None:
    """后台任务：每 INTERVAL 秒自检一轮，顺手清掉过期记录。"""
    await asyncio.sleep(FIRST_DELAY)
    while True:
        with contextlib.suppress(Exception):
            await run_once(parse)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(prune)
        await asyncio.sleep(INTERVAL)


def prune() -> int:
    with _db() as conn:
        return conn.execute("DELETE FROM probes WHERE ts < ?", (time.time() - RETENTION_DAYS * 86400,)).rowcount


# --------------------------------------------------------------------------- 页面数据


def _day(ts: float) -> int:
    return int((ts + TZ_OFFSET) // 86400)


def _user_stats(since: float) -> dict[str, dict]:
    """真实用户近 7 天按平台的解析次数和成功率（作品删了、链接贴错这类不算平台的账）。"""
    if not stats.DB_PATH.exists():
        return {}
    marks = ",".join("?" * len(_USER_SIDE))
    with db.transaction(stats.DB_PATH, stats._SCHEMA) as conn:
        rows = conn.execute(
            f"SELECT source, COUNT(*), SUM(ok) FROM events WHERE kind = 'parse' AND ts >= ? "
            f"AND reason NOT IN ({marks}) GROUP BY source",
            (since, *_USER_SIDE),
        ).fetchall()
    return {s: {"n": n, "ok": ok or 0} for s, n, ok in rows}


def snapshot(now: float | None = None) -> dict:
    """/status 页要的全部数据。"""
    now = now or time.time()
    since = now - DAYS * 86400
    rows = []
    if DB_PATH.exists():
        with _db() as conn:
            rows = conn.execute(
                "SELECT ts, platform, ok, reason, ms FROM probes WHERE ts >= ? ORDER BY ts", (since,)
            ).fetchall()
    users = _user_stats(now - 7 * 86400)
    today = _day(now)
    days = list(range(today - DAYS + 1, today + 1))
    out = []
    for p in PLATFORMS:
        mine = [r for r in rows if r[1] == p.key and r[3] != "canary"]
        last = mine[-1] if mine else None
        week = [r for r in mine if r[0] >= now - 7 * 86400]
        per_day = {d: [0, 0] for d in days}
        for ts, _, ok, _, _ in mine:
            if (d := _day(ts)) in per_day:
                per_day[d][0] += 1
                per_day[d][1] += ok
        ok_ms = [r[4] for r in week if r[2]]
        u = users.get(p.source) if p.source else None
        out.append(
            {
                "platform": p,
                "last": {"ts": last[0], "ok": bool(last[2]), "reason": last[3], "ms": last[4]} if last else None,
                "week_n": len(week),
                "week_ok": sum(r[2] for r in week),
                "median_ms": int(statistics.median(ok_ms)) if ok_ms else 0,
                "days": [{"day": d, "n": per_day[d][0], "ok": per_day[d][1]} for d in days],
                "users": u if u and u["n"] else None,
            }
        )
    checked = [x for x in out if x["last"]]
    return {
        "now": now,
        "platforms": out,
        "checked_at": max((x["last"]["ts"] for x in checked), default=0),
        "down": [x["platform"].label for x in checked if not x["last"]["ok"]],
        "n_checked": len(checked),
    }


def bj_time(ts: float, now: float | None = None) -> str:
    """北京时间：今天的只写几点几分，往前的带上日期。"""
    t = time.gmtime(ts + TZ_OFFSET)
    if _day(ts) == _day(now or time.time()):
        return f"{t.tm_hour:02d}:{t.tm_min:02d}"
    return f"{t.tm_mon} 月 {t.tm_mday} 日 {t.tm_hour:02d}:{t.tm_min:02d}"


def day_label(day: int) -> str:
    t = time.gmtime(day * 86400)
    return f"{t.tm_mon} 月 {t.tm_mday} 日"


def spaced(label: str) -> str:
    """中文里夹英文名前后空一格：「去解析 YouTube 链接」「去解析抖音链接」。"""
    return f" {label} " if label[:1].isascii() else label


def headline(snap: dict) -> str:
    """页面第一句话，也拿去当 description：搜索结果里直接看到现在的状态。"""
    n = snap["n_checked"]
    if not n:
        return "自检数据还在收集中：服务启动 5 分钟后开始第一轮，之后每小时一次。"
    when = bj_time(snap["checked_at"], snap["now"])
    if not snap["down"]:
        return f"{when} 检测：{n} 个平台全部能正常解析。"
    down = "、".join(snap["down"])
    gap = " " if down[-1:].isascii() else ""  # 「YouTube 解析失败」「抖音图文解析失败」
    return f"{when} 检测：{down}{gap}解析失败，其余 {n - len(snap['down'])} 个正常。"
