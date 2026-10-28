"""搜索平台报表快照，与站内访客 / 来源数据库隔离。"""

from __future__ import annotations

import json
import math
import time
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

from . import db
from .convert import config
from .windsor import ReportError

DB_PATH = config.DATA_DIR / "search_reports.db"
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS snapshots (
    provider TEXT NOT NULL, account TEXT NOT NULL, synced REAL NOT NULL,
    date_from TEXT NOT NULL, date_to TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(provider, account)
);
CREATE TABLE IF NOT EXISTS sync_status (
    provider TEXT NOT NULL, account TEXT NOT NULL, attempted REAL NOT NULL,
    error TEXT NOT NULL, PRIMARY KEY(provider, account)
);
"""


def initialize() -> None:
    # 首次启用 WAL / 建表需先串行完成；两个平台同时创建新库可能触发 SQLITE_BUSY。
    with db.transaction(DB_PATH, SCHEMA):
        pass


def _number(value, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ReportError("invalid_data")
    if not math.isfinite(value) or value < 0 or (integer and int(value) != value):
        raise ReportError("invalid_data")
    return int(value) if integer else value


def _dimension(row: dict, kind: str) -> str:
    if kind == "daily":
        return ""
    value = row.get("query" if kind == "queries" else "page")
    if not isinstance(value, str) or len(value) > 4096:
        raise ReportError("invalid_data")
    if kind == "pages":
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
                raise ValueError
            # 报表里的页面也不落查询参数；查询词仅来自平台的聚合 query 字段。
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
        except ValueError:
            raise ReportError("invalid_data") from None
    return value


def normalize(rows: list[dict], kind: str, account: str, start: str, end: str) -> list[dict]:
    normalized = []
    expected_site = account.removeprefix("sc-domain:").rstrip("/")
    for row in rows:
        day = row.get("date", "")
        try:
            valid_day = date.fromisoformat(day).isoformat() == day
        except (ValueError, TypeError):
            valid_day = False
        if not valid_day or not start <= day <= end:
            raise ReportError("invalid_data")
        if str(row.get("site", "")).removeprefix("sc-domain:").rstrip("/") != expected_site:
            raise ReportError("wrong_site")
        normalized.append(
            {
                "date": day,
                "label": _dimension(row, kind),
                "clicks": _number(row.get("clicks"), integer=True),
                "impressions": _number(row.get("impressions"), integer=True),
                "position": _number(row["position"]) if row.get("position") is not None else None,
            }
        )
    return normalized


def status(provider: str, account: str) -> dict:
    if not DB_PATH.exists():
        return {}
    with db.transaction(DB_PATH, SCHEMA, rows=True) as conn:
        row = conn.execute(
            "SELECT attempted, error FROM sync_status WHERE provider=? AND account=?", (provider, account)
        ).fetchone()
    return dict(row) if row else {}


def mark_attempt(provider: str, account: str, error: str = "") -> None:
    with db.transaction(DB_PATH, SCHEMA) as conn:
        conn.execute("INSERT OR REPLACE INTO sync_status VALUES (?, ?, ?, ?)", (provider, account, time.time(), error))


def save(provider: str, account: str, start: str, end: str, reports: dict) -> None:
    now = time.time()
    with db.transaction(DB_PATH, SCHEMA) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (provider, account, now, start, end, json.dumps(reports, ensure_ascii=False, allow_nan=False)),
        )
        conn.execute("INSERT OR REPLACE INTO sync_status VALUES (?, ?, ?, '')", (provider, account, now))
        # 换绑站点后不继续保留上一个站点的关键词快照。
        conn.execute("DELETE FROM snapshots WHERE provider=? AND account!=?", (provider, account))
        conn.execute("DELETE FROM sync_status WHERE provider=? AND account!=?", (provider, account))


def snapshot(provider: str, account: str) -> dict | None:
    if not DB_PATH.exists():
        return None
    with db.transaction(DB_PATH, SCHEMA, rows=True) as conn:
        row = conn.execute(
            "SELECT synced, date_from, date_to, payload FROM snapshots WHERE provider=? AND account=?",
            (provider, account),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["reports"] = json.loads(result.pop("payload"))
    return result


def metrics(rows: list[dict]) -> dict:
    clicks = sum(row["clicks"] for row in rows)
    impressions = sum(row["impressions"] for row in rows)
    ranked = [row for row in rows if row["impressions"] and row["position"] is not None]
    weight = sum(row["impressions"] for row in ranked)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else None,
        "position": sum(row["position"] * row["impressions"] for row in ranked) / weight
        if weight and weight == impressions
        else None,
    }


def _in_range(rows: list[dict], start: str, end: str) -> list[dict]:
    return [row for row in rows if start <= row["date"] <= end]


def _ranked(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["label"], []).append(row)
    result = [{"label": label, **metrics(group)} for label, group in groups.items()]
    return sorted(result, key=lambda row: (-row["clicks"], -row["impressions"], row["label"]))[:20]


def summarize(saved: dict, days: int) -> dict:
    reports = saved["reports"]
    daily = reports["daily"]["rows"]
    latest = max((row["date"] for row in daily), default=None)
    base = {"synced": saved["synced"], "latest": latest, "requested_to": saved["date_to"]}
    if not latest:
        return {**base, "totals": None, "daily": [], "queries": [], "pages": []}
    end = date.fromisoformat(latest)
    start = end - timedelta(days=days - 1)
    previous_start, previous_end = start - timedelta(days=days), start - timedelta(days=1)
    selected = _in_range(daily, start.isoformat(), latest)
    previous = _in_range(daily, previous_start.isoformat(), previous_end.isoformat())
    return {
        **base,
        "date_from": start.isoformat(),
        "date_to": latest,
        "previous_from": previous_start.isoformat(),
        "previous_to": previous_end.isoformat(),
        "previous": metrics(previous) if previous and previous_start.isoformat() >= saved["date_from"] else None,
        "totals": metrics(selected),
        "daily": [
            {"date": day, **metrics([r for r in selected if r["date"] == day])}
            for day in sorted({r["date"] for r in selected})
        ],
        "queries": _ranked(_in_range(reports["queries"]["rows"], start.isoformat(), latest)),
        "pages": _ranked(_in_range(reports.get("pages", {}).get("rows", []), start.isoformat(), latest)),
        "limited": any(report["limited"] for report in reports.values()),
    }
