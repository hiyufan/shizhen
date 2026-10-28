"""统计受众分类；原始事件保留，缺少证据的请求不能默认认作真人。"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass

from .convert import net

VERSION = 1
CLASSES = ("human", "unknown", "machine")
COOKIE = "qyt_activity"
MAX_AGE = 1800
# 精选产品标识，不直接引入通用库中的 news、Android Build 号等宽泛规则。
# 依据与回归样例见 docs/analytics.md；规则只作用于统计，不阻止页面访问。
_AUTOMATION = re.compile(
    r"GoogleOther|Google-InspectionTool|HeadlessChrome|PhantomJS|Playwright|Selenium|"
    r"ChatGPT-User|Claude-User|Perplexity-User|LarkUrl|monetag verification agent|DNS-Brand-Probe|"
    r"\b(?:bot|spider|crawler)\b|curl/|wget/|python[-/ ]|python-urllib|"
    r"^(?:node|n8n)(?:[/\s]|$)",
    re.I,
)
_BROWSER = re.compile(r"^Mozilla/.*(?:\(KHTML, like Gecko\)|Gecko/\d+.*Firefox/)", re.I)


@dataclass(frozen=True)
class Evidence:
    status: str = "unknown"
    reason: str = "insufficient"


def agent_evidence(agent: str) -> Evidence:
    if _AUTOMATION.search(agent):
        return Evidence("machine", "automation-agent")
    return Evidence()


def browser_agent(agent: str) -> bool:
    return bool(_BROWSER.search(agent)) and agent_evidence(agent).status != "machine"


def _signature(payload: str) -> str:
    return hmac.new(net.secret_key(), b"audience-v1:" + payload.encode(), hashlib.sha256).hexdigest()


def activity_cookie(visit: str, status: str) -> str:
    payload = f"{visit}.{status}.{int(time.time()) + MAX_AGE}"
    return payload + "." + _signature(payload)


def activity_evidence(cookie: str, visit: str) -> Evidence:
    parts = cookie.split(".")
    if len(parts) != 4 or parts[0] != visit or parts[1] not in ("human", "machine"):
        return Evidence()
    payload, signature = cookie.rsplit(".", 1)
    if not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(signature, _signature(payload)):
        return Evidence()
    try:
        valid = time.time() < int(parts[2]) <= time.time() + MAX_AGE + 1
    except ValueError:
        valid = False
    return Evidence(parts[1], "browser-interaction") if valid else Evidence()


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS event_audience ("
    " event_id INTEGER PRIMARY KEY, traffic_id INTEGER, visit TEXT NOT NULL DEFAULT '',"
    " status TEXT NOT NULL, reason TEXT NOT NULL, version INTEGER NOT NULL);"
    "CREATE UNIQUE INDEX IF NOT EXISTS audience_traffic ON event_audience (traffic_id);"
    "CREATE INDEX IF NOT EXISTS audience_visit ON event_audience (visit);"
    "CREATE TABLE IF NOT EXISTS audience_sessions ("
    " visit TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL, updated REAL NOT NULL);"
    "CREATE VIEW IF NOT EXISTS audience_events AS "
    "SELECT e.rowid AS event_id, e.*, COALESCE(q.status, 'unknown') AS audience "
    "FROM events e LEFT JOIN event_audience q ON q.event_id = e.rowid;"
    "CREATE VIEW IF NOT EXISTS audience_traffic_events AS "
    "SELECT e.*, COALESCE(q.status, 'unknown') AS audience "
    "FROM traffic_events e LEFT JOIN event_audience q ON q.traffic_id = e.rowid;"
)


def event_table(selected: str, *, traffic: bool = False) -> str:
    if selected not in (*CLASSES, "all"):
        raise ValueError("Unknown audience")
    table = "traffic_events" if traffic else "events"
    if selected == "all":
        return table
    return f"(SELECT * FROM audience_{table} WHERE audience = '{selected}')"


def write_event(conn, event_id: int, traffic_id: int | None, visit: str, evidence: Evidence) -> None:
    session = conn.execute("SELECT status, reason FROM audience_sessions WHERE visit = ?", (visit,)).fetchone()
    if session and evidence.status != "machine":
        evidence = Evidence(*session)
    conn.execute(
        "INSERT INTO event_audience VALUES (?, ?, ?, ?, ?, ?)",
        (event_id, traffic_id, visit, evidence.status, evidence.reason, VERSION),
    )


def write_signals(conn, signals: list[tuple]) -> None:
    for visit, status, reason, updated in signals:
        previous = conn.execute("SELECT status FROM audience_sessions WHERE visit = ?", (visit,)).fetchone()
        if previous and (previous[0] == "machine" or (previous[0] == "unknown" and status != "machine")):
            continue  # 自动化或异常会话不能靠重放交互打点重新变回真人。
        conn.execute(
            "INSERT INTO audience_sessions VALUES (?, ?, ?, ?) ON CONFLICT(visit) DO UPDATE SET "
            "status = excluded.status, reason = excluded.reason, updated = excluded.updated",
            (visit, status, reason, updated),
        )
        conn.execute(
            "UPDATE event_audience SET status = ?, reason = ?, version = ? WHERE visit = ? AND status != 'machine'",
            (status, reason, VERSION, visit),
        )


def check_rates(conn, visits: set[str]) -> None:
    # 仅检查独立解析；轮询、缓存重放、媒体分片和广告请求不是主动操作。
    for visit in visits - {""}:
        times = conn.execute(
            "SELECT e.ts FROM events e JOIN event_audience q ON q.event_id = e.rowid "
            "WHERE q.visit = ? AND e.kind = 'parse' AND e.reason != 'cache' ORDER BY e.ts",
            (visit,),
        ).fetchall()
        if any(times[i][0] - times[i - 19][0] < 60 for i in range(19, len(times))):
            write_signals(conn, [(visit, "unknown", "rapid-actions", time.time())])


def summary(conn, since: float, until: float) -> dict:
    counts = {status: {"events": 0, "ips": 0, "active_ips": 0} for status in CLASSES}
    for status, events, ips, active in conn.execute(
        "SELECT audience, COUNT(*), COUNT(DISTINCT ip), "
        "COUNT(DISTINCT CASE WHEN kind IN ('parse', 'job') THEN ip END) "
        "FROM audience_events WHERE ts >= ? AND ts < ? GROUP BY audience",
        (since, until),
    ):
        counts[status] = {"events": events, "ips": ips, "active_ips": active}
    reasons = conn.execute(
        "SELECT COALESCE(q.status, 'unknown'), COALESCE(q.reason, 'unclassified'), COUNT(*) "
        "FROM events e LEFT JOIN event_audience q ON q.event_id = e.rowid "
        "WHERE e.ts >= ? AND e.ts < ? GROUP BY 1, 2 ORDER BY 3 DESC",
        (since, until),
    ).fetchall()
    return {
        "version": VERSION,
        "counts": counts,
        "reasons": [dict(zip(("status", "reason", "events"), r, strict=True)) for r in reasons],
    }


def prune(conn) -> None:
    conn.execute("DELETE FROM event_audience WHERE event_id NOT IN (SELECT rowid FROM events)")
    conn.execute("DELETE FROM audience_sessions WHERE visit NOT IN (SELECT visit FROM event_audience)")
