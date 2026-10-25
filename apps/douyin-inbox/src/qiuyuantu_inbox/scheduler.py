"""按发送者持久轮转；消息额度与浏览器耗时共同约束发送公平性。"""

import json
import math

SCHEDULER_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler (
 phase TEXT NOT NULL, sender TEXT NOT NULL, credit REAL NOT NULL DEFAULT 0,
 turn INTEGER NOT NULL, PRIMARY KEY(phase,sender)
);
CREATE TABLE IF NOT EXISTS service_costs (kind TEXT PRIMARY KEY, seconds REAL NOT NULL);
"""


def message_cost(payload: dict) -> int:
    return 1 if payload.get("live_pair") else max(1, len(payload.get("files", [])))


def payload_kind(payload: dict) -> str:
    if "text" in payload:
        return "control"
    if payload.get("live_pair") or payload.get("live_vid"):
        return "live"
    return "video" if payload.get("video") else "image"


def estimated_cost(db, payload: dict, interval: float) -> float:
    row = db.execute("SELECT seconds FROM service_costs WHERE kind=?", (payload_kind(payload),)).fetchone()
    return max(message_cost(payload), min(36, row[0] / interval) if row else 1)


def candidates(db, phase: str, now: float, scope: set[str] | None) -> list[dict]:
    clause = " AND j.conversation IN (" + ",".join("?" for _ in scope) + ")" if scope else ""
    busy = " AND NOT EXISTS (SELECT 1 FROM jobs b WHERE b.sender=j.sender AND b.state='processing')"
    rows = db.execute(
        "SELECT j.* FROM jobs j WHERE j.state=? AND j.available<=?"
        + clause
        + (busy if phase == "queued" else "")
        + " ORDER BY j.received,j.id",
        (phase, now, *sorted(scope or [])),
    ).fetchall()
    return [dict(row) for row in rows]


def runnable_parts(db, rows: list[dict], pipeline: bool = False) -> list[dict]:
    result = []
    active = {}
    for row in db.execute(
        "SELECT j.sender,j.id FROM jobs j JOIN parts p ON p.job=j.id "
        "WHERE j.state IN ('ready','sending') AND p.state IN ('sent','sending') "
        "AND (json_type(p.payload,'$.files')='array' OR json_type(p.payload,'$.forward_source')='text') "
        "GROUP BY j.id ORDER BY j.received,j.id"
    ):
        active.setdefault(row[0], row[1])
    for row in rows:
        part = db.execute(
            "SELECT payload,number FROM parts WHERE job=? AND state!='sent' ORDER BY number LIMIT 1", (row["id"],)
        ).fetchone()
        if pipeline and (not part or not uploaded_part(db, row, part[1])):
            continue
        payload = json.loads(part[0]) if part else {"text": ""}
        if payload_kind(payload) != "control" and active.get(row["sender"], row["id"]) != row["id"]:
            continue
        row["dispatch_payload"] = payload
        result.append(row)
    return result


def uploaded_part(db, job: dict, number: int) -> bool:
    ready = db.execute(
        "SELECT 1 FROM outbound WHERE job=? AND number=? AND state='uploaded'", (job["id"], number)
    ).fetchone()
    busy = db.execute(
        "SELECT 1 FROM outbound o JOIN jobs j ON j.id=o.job WHERE o.state='submitted' AND j.sender=? LIMIT 1",
        (job["sender"],),
    ).fetchone()
    return bool(ready and not busy)


def choose_lane(db, rows: list[dict]) -> list[dict]:
    controls = [row for row in rows if payload_kind(row["dispatch_payload"]) == "control"]
    media = [row for row in rows if payload_kind(row["dispatch_payload"]) != "control"]
    previous = db.execute("SELECT value FROM meta WHERE key='control_streak'").fetchone()
    streak = int(previous[0]) if previous else 0
    use_control = bool(controls) and (streak < 2 or not media)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('control_streak',?)", (str(streak + 1 if use_control else 0),))
    return controls if use_control else media


def pick(db, phase: str, now: float, scope: set[str] | None, interval: float, pipeline: bool = False) -> dict | None:
    rows = candidates(db, phase, now, scope)
    if phase == "ready":
        rows = choose_lane(db, runnable_parts(db, rows, pipeline)) if rows else []
    if not rows:
        return None
    clock = db.execute("SELECT coalesce(max(turn),0) FROM scheduler WHERE phase=?", (phase,)).fetchone()[0]
    heads = {}
    for row in rows:
        heads.setdefault(row["sender"], row)
        db.execute("INSERT OR IGNORE INTO scheduler(phase,sender,turn) VALUES (?,?,?)", (phase, row["sender"], clock))
    turns = {
        row["sender"]: dict(row)
        for row in db.execute("SELECT * FROM scheduler WHERE phase=?", (phase,))
        if row["sender"] in heads
    }
    order = sorted(heads, key=lambda sender: (turns[sender]["turn"], heads[sender]["received"], sender))
    return pick_affordable(db, phase, heads, turns, order, clock, interval)


def pick_affordable(db, phase, heads, turns, order, clock, interval):
    costs = {
        sender: estimated_cost(db, row["dispatch_payload"], interval) if phase == "ready" else 1
        for sender, row in heads.items()
    }
    rounds = math.ceil(max(costs[sender] - turns[sender]["credit"] for sender in order)) + 1
    for _ in range(max(1, rounds)):
        for sender in order:
            credit = min(36, turns[sender]["credit"] + 1)
            turns[sender]["credit"] = credit
            db.execute("UPDATE scheduler SET credit=? WHERE phase=? AND sender=?", (credit, phase, sender))
            if credit >= costs[sender]:
                db.execute(
                    "UPDATE scheduler SET credit=?,turn=? WHERE phase=? AND sender=?",
                    (credit - costs[sender], clock + 1, phase, sender),
                )
                return heads[sender]
    return None


def charge(db, sender: str, payload: dict, elapsed: float, interval: float) -> None:
    kind = payload_kind(payload)
    estimate = estimated_cost(db, payload, interval)
    actual = max(message_cost(payload), min(36, elapsed / interval))
    db.execute(
        "UPDATE scheduler SET credit=max(-36,min(36,credit+?)) WHERE phase='ready' AND sender=?",
        (estimate - actual, sender),
    )
    db.execute(
        "INSERT INTO service_costs VALUES (?,?) ON CONFLICT(kind) DO UPDATE "
        "SET seconds=0.8*seconds+0.2*excluded.seconds",
        (kind, elapsed),
    )
