"""从本地访问日志回溯受众分类；只写旁表，不改写或删除原始事件。"""

from __future__ import annotations

import argparse
import bisect
import collections
import contextlib
import datetime as dt
import gzip
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import attribution, audience, seo, stats, traffic

_LINE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(.*?)" (\d+) \S+ "(.*?)" "(.*?)"')
_PARSE_PATHS = {"/api/parse", "/api/parse/stream"}
_ACTIONS = _PARSE_PATHS | {"/api/convert", "/api/live", "/api/download", "/api/prepare", "/api/upload"}
_DOWNLOAD = re.compile(r"^/api/(?:proxy|download-hit|jobs/[\w-]+/file)$")


@dataclass
class LogRequest:
    ts: float
    ip: str
    agent: str
    path: str
    kind: str
    same_site: bool
    session: int = -1


@dataclass
class Session:
    ip: str
    agent: str
    start: float
    end: float
    evidence: audience.Evidence = field(default_factory=audience.Evidence)


def _request(line: str, hashes: dict, hosts: set[str]) -> LogRequest | None:
    match = _LINE.match(line)
    if not match:
        return None
    ip, stamp, request, status, referrer, agent = match.groups()
    parts = request.split(" ")
    if len(parts) != 3 or parts[0] not in {"GET", "POST"} or status not in {"200", "304"}:
        return None
    try:
        ts = dt.datetime.strptime(stamp, "%d/%b/%Y:%H:%M:%S %z").timestamp()
        path = urlsplit(parts[1]).path
    except ValueError:
        return None
    if ip not in hashes:
        hashes[ip] = stats.hash_ip(ip)
    same_site = attribution.hostname(referrer) in hosts
    return LogRequest(ts, hashes[ip], agent[:2048], path, "", same_site)


def _kind(path: str, pages: set[str]) -> str:
    if path in pages:
        return "view"
    if path in _PARSE_PATHS:
        return "parse"
    if path.startswith("/js/"):
        return "script"
    if _DOWNLOAD.fullmatch(path):
        return "download"
    if re.fullmatch(r"/api/jobs/[\w-]+", path):
        return "poll"
    return "action" if path in _ACTIONS else ""


def read_logs(paths: list[Path], events: list, hosts: set[str]) -> list[LogRequest]:
    if not events:
        return []
    ips = {event[3] for event in events}
    since, until = min(event[1] for event in events) - 1800, max(event[1] for event in events) + 2
    pages = set(seo.all_paths()) | {e[4] for e in events if e[2] == "view"}
    hashes, records, seen = {}, [], set()
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                row = _request(line, hashes, hosts)
                if row is None or row.ip not in ips or not since <= row.ts <= until:
                    continue
                row.kind = _kind(row.path, pages)
                key = (row.ts, row.ip, row.agent, row.path, row.kind, row.same_site)
                if row.kind and key not in seen:
                    seen.add(key)
                    records.append(row)
    return sorted(records, key=lambda r: r.ts)


def sessions(records: list[LogRequest]) -> list[Session]:
    active, result, evidence = {}, [], {}
    for row in records:
        key = (row.ip, row.agent)
        index = active.get(key)
        if index is None or row.ts - result[index].end > 1800:
            index = len(result)
            active[key] = index
            result.append(Session(row.ip, row.agent, row.ts, row.ts, audience.agent_evidence(row.agent)))
            evidence[index] = {}
        row.session = index
        session = result[index]
        session.end = row.ts
        _session_evidence(session, evidence[index], row)
    return result


def _session_evidence(session: Session, times: dict, row: LogRequest) -> None:
    if row.kind in {"view", "script"}:
        times[row.kind] = row.ts
    if not row.same_site or row.path not in _ACTIONS or not audience.browser_agent(row.agent):
        return
    # 历史不能补造交互事件。只有同一浏览器的页面、模块和主动接口链路才给出较强证据。
    if all(0 <= row.ts - times.get(kind, float("-inf")) <= 1800 for kind in ("view", "script")):
        session.evidence = audience.Evidence("human", "historical-browser-action")


def _indexes(records: list[LogRequest], groups: list[Session]) -> tuple[dict, dict]:
    requests, by_ip = collections.defaultdict(list), collections.defaultdict(list)
    for row in records:
        path = row.path if row.kind == "view" else ""
        requests[(row.ip, row.kind, path)].append(row)
    for index, group in enumerate(groups):
        by_ip[group.ip].append((index, group))
    return requests, by_ip


def _nearby(rows: list[LogRequest], stamp: float) -> list[LogRequest]:
    times = [row.ts for row in rows]
    return rows[bisect.bisect_left(times, stamp - 2) : bisect.bisect_right(times, stamp + 2)]


def classify(event: tuple, indexes: tuple[dict, dict], groups: list[Session]) -> tuple[audience.Evidence, int | None]:
    _event_id, stamp, kind, ip, source, _ok, _reason, _ms = event
    requests, by_ip = indexes
    nearby = _nearby(requests.get((ip, kind, source if kind == "view" else ""), []), stamp)
    agents = {row.agent for row in nearby}
    if len(agents) > 1:
        return audience.Evidence("unknown", "historical-ambiguous"), None
    if nearby:
        group_id = min(nearby, key=lambda row: abs(row.ts - stamp)).session
        group = groups[group_id]
        result = group.evidence
        if result.status == "machine":
            result = audience.Evidence("machine", "historical-automation")
        elif result.status == "unknown":
            result = audience.Evidence("unknown", "historical-insufficient")
        return result, group_id
    if kind not in {"job", "download"}:
        return audience.Evidence("unknown", "historical-missing"), None
    candidates = [(i, group) for i, group in by_ip.get(ip, []) if group.start <= stamp <= group.end + 2]
    if len(candidates) == 1 and candidates[0][1].evidence.status == "human":
        return candidates[0][1].evidence, candidates[0][0]
    return audience.Evidence("unknown", "historical-ambiguous"), None


def classifications(events: list, records: list[LogRequest]) -> dict[int, audience.Evidence]:
    groups = sessions(records)
    indexes = _indexes(records, groups)
    results, parse_times = {}, collections.defaultdict(list)
    for event in events:
        evidence, group = classify(event, indexes, groups)
        results[event[0]] = (evidence, group)
        if group is not None and event[2] == "parse" and event[6] != "cache":
            parse_times[group].append(event[1])
    rapid = set()
    for group, times in parse_times.items():
        times.sort()
        if any(times[i] - times[i - 19] < 60 for i in range(19, len(times))):
            rapid.add(group)
    return {
        event_id: audience.Evidence("unknown", "rapid-actions")
        if group in rapid and result.status == "human"
        else result
        for event_id, (result, group) in results.items()
    }


def _traffic_links(conn) -> dict:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'traffic_events'").fetchone()
    links = collections.defaultdict(list)
    if exists:
        for row in conn.execute("SELECT rowid, ts, visit, kind, source, ok FROM traffic_events"):
            traffic_id, stamp, visit, kind, source, ok = row
            links[(stamp, kind, source, ok)].append((traffic_id, visit))
    return links


def snapshot(path: Path) -> tuple[list, dict]:
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("BEGIN")
        events = conn.execute("SELECT rowid, ts, kind, ip, source, ok, reason, ms FROM events ORDER BY ts").fetchall()
        return events, _traffic_links(conn)


def report(events: list, labels: dict[int, audience.Evidence]) -> dict:
    result = {status: {"events": 0, "ips": set(), "active_ips": set()} for status in audience.CLASSES}
    reasons = collections.Counter()
    for event in events:
        evidence = labels[event[0]]
        row = result[evidence.status]
        row["events"] += 1
        row["ips"].add(event[3])
        if event[2] in {"parse", "job"}:
            row["active_ips"].add(event[3])
        reasons[evidence.reason] += 1
    for row in result.values():
        row["ips"], row["active_ips"] = len(row["ips"]), len(row["active_ips"])
    return {"version": audience.VERSION, "events": len(events), "classes": result, "reasons": dict(reasons)}


def apply(path: Path, events: list, links: dict, labels: dict[int, audience.Evidence]) -> Path:
    backup = path.with_name(
        path.name + ".before-audience-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    # SQLite 在线备份同时包含 WAL；普通文件复制可能丢掉刚落盘的事务。
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with contextlib.closing(sqlite3.connect(path)) as conn, contextlib.closing(sqlite3.connect(backup)) as target:
        conn.backup(target)
    with contextlib.closing(sqlite3.connect(path, timeout=30)) as conn, conn:
        conn.executescript(stats._SCHEMA + traffic.SCHEMA + audience.SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        _write_history(conn, events, links, labels)
    return backup


def _write_history(conn, events: list, links: dict, labels: dict[int, audience.Evidence]) -> None:
    for event in events:
        event_id, stamp, kind, ip, source, ok, reason, ms = event
        current = conn.execute(
            "SELECT ts, kind, ip, source, ok, reason, ms FROM events WHERE rowid = ?", (event_id,)
        ).fetchone()
        if current != (stamp, kind, ip, source, ok, reason, ms):
            continue  # 清理任务删掉或替换过的行不能被旧快照的 rowid 重新标记。
        matches = links.get((stamp, kind, source, ok), [])
        traffic_id, visit = matches[0] if len(matches) == 1 else (None, "")
        evidence = labels[event_id]
        conn.execute(
            "INSERT INTO event_audience VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(event_id) DO UPDATE SET "
            "traffic_id = excluded.traffic_id, visit = excluded.visit, status = excluded.status, "
            "reason = excluded.reason, version = excluded.version "
            "WHERE event_audience.reason LIKE 'historical-%' "
            "OR event_audience.reason = 'unclassified'",
            (event_id, traffic_id, visit, evidence.status, evidence.reason, audience.VERSION),
        )


def stored_report(path: Path) -> dict:
    events, _links = snapshot(path)
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        stored = {
            event_id: audience.Evidence(status, reason)
            for event_id, status, reason in conn.execute("SELECT event_id, status, reason FROM event_audience")
        }
    labels = {event[0]: stored.get(event[0], audience.Evidence("unknown", "unclassified")) for event in events}
    return report(events, labels)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=stats.DB_PATH)
    parser.add_argument(
        "--log", type=Path, action="append", default=[], help="本站 combined 格式日志，可重复，支持 .gz"
    )
    parser.add_argument("--host", action="append", default=[], help="本站来源域名，可重复")
    parser.add_argument("--apply", action="store_true", help="先备份，再写分类旁表；默认只输出预览")
    args = parser.parse_args()
    events, links = snapshot(args.database)
    hosts = set(args.host) or {attribution.hostname(seo.SITE_URL)} - {""}
    records = read_logs(args.log, events, hosts)
    labels = classifications(events, records)
    result = report(events, labels)
    if args.apply:
        backup = apply(args.database, events, links, labels)
        # 在线证据优先，应用结果必须按实际保留的分类汇总，不能再次输出推测覆盖结果。
        result = stored_report(args.database)
        result.update({"backup": str(backup), "processed_events": len(events)})
    result["log_requests"] = len(records)
    print(json.dumps(result, ensure_ascii=False, indent=2))
