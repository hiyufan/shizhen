"""按访问会话汇总推广效果；旧事件保持原样，不推测历史来源。"""

from __future__ import annotations

from .attribution import Visit

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS traffic_visits ("
    " id TEXT PRIMARY KEY, started REAL NOT NULL, ip TEXT NOT NULL, channel TEXT NOT NULL,"
    " source TEXT NOT NULL, medium TEXT NOT NULL, campaign TEXT NOT NULL, landing TEXT NOT NULL);"
    "CREATE INDEX IF NOT EXISTS traffic_visits_started ON traffic_visits (started);"
    "CREATE TABLE IF NOT EXISTS traffic_events ("
    " ts REAL NOT NULL, visit TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, ok INTEGER NOT NULL);"
    "CREATE INDEX IF NOT EXISTS traffic_events_visit ON traffic_events (visit, ts);"
    "CREATE INDEX IF NOT EXISTS traffic_events_ts ON traffic_events (ts);"
    "CREATE TABLE IF NOT EXISTS traffic_meta (key TEXT PRIMARY KEY, value REAL NOT NULL);"
)


def write(conn, rows: list[tuple[tuple, Visit | None]]) -> None:
    if not rows:
        return
    conn.execute(
        "INSERT INTO traffic_meta VALUES ('started', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = MIN(value, excluded.value)",
        (min(row[0][0] for row in rows),),
    )
    visits, events = [], []
    for event, visit in rows:
        ts, kind, ip, source, ok, _reason, _ms = event
        if kind == "view" and visit:
            visits.append(
                (visit.id, visit.started, ip, visit.channel, visit.source, visit.medium, visit.campaign, visit.landing)
            )
        events.append((ts, visit.id if visit else "", kind, source, ok))
    conn.executemany("INSERT OR IGNORE INTO traffic_visits VALUES (?, ?, ?, ?, ?, ?, ?, ?)", visits)
    conn.executemany("INSERT INTO traffic_events VALUES (?, ?, ?, ?, ?)", events)


def prune(conn, cutoff: float) -> None:
    conn.execute("DELETE FROM traffic_events WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM traffic_visits WHERE started < ?", (cutoff,))


_COHORT = """
SELECT v.channel, v.source, v.medium, v.campaign, v.landing, v.ip,
       SUM(CASE WHEN e.kind = 'view' THEN 1 ELSE 0 END),
       MAX(CASE WHEN e.kind = 'parse' AND e.ok = 1 THEN 1 ELSE 0 END),
       MAX(CASE WHEN e.kind = 'download' THEN 1 ELSE 0 END),
       MAX(CASE WHEN e.kind = 'job' AND e.ok = 1 AND
           e.source IN ('gif', 'livephoto', 'motionphoto', 'live') THEN 1 ELSE 0 END)
FROM traffic_visits v LEFT JOIN traffic_events e ON e.visit = v.id AND e.ts < ?
WHERE v.started >= ? AND v.started < ? GROUP BY v.id
"""


def _groups(rows: list, indices: tuple[int, ...], names: tuple[str, ...]) -> list[dict]:
    groups = {}
    for row in rows:
        key = tuple(row[i] for i in indices)
        group = groups.setdefault(
            key,
            {
                **dict(zip(names, key, strict=True)),
                "visits": 0,
                "views": 0,
                "parsed": 0,
                "downloaded": 0,
                "converted": 0,
                "ips": set(),
            },
        )
        group["visits"] += 1
        group["ips"].add(row[5])
        for name, value in zip(("views", "parsed", "downloaded", "converted"), row[6:], strict=True):
            group[name] += value or 0
    result = []
    for group in groups.values():
        group["users"] = len(group.pop("ips"))
        result.append(group)
    return sorted(result, key=lambda item: item["visits"], reverse=True)


def summary(conn, since: float, until: float) -> dict:
    rows = conn.execute(_COHORT, (until, since, until)).fetchall()
    first = conn.execute("SELECT value FROM traffic_meta WHERE key = 'started'").fetchone()
    total = conn.execute("SELECT COUNT(*) FROM events WHERE ts >= ? AND ts < ?", (since, until)).fetchone()[0]
    measured, unassigned = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(visit = ''), 0) FROM traffic_events WHERE ts >= ? AND ts < ?",
        (since, until),
    ).fetchone()
    totals = _groups(rows, (), ())
    return {
        "started": first[0] if first else None,
        "historical_events": max(0, total - measured),
        "unassigned_events": unassigned,
        "totals": totals[0]
        if totals
        else dict.fromkeys(("visits", "views", "parsed", "downloaded", "converted", "users"), 0),
        "channels": _groups(rows, (0, 1, 2, 3), ("channel", "source", "medium", "campaign")),
        "landing_pages": _groups(rows, (4,), ("landing",)),
        "search_pages": _groups([row for row in rows if row[0] == "search"], (1, 4), ("source", "landing")),
    }
