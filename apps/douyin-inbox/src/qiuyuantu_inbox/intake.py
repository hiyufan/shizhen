"""入站事件先落盘，再幂等应用；页面确认不领先数据库提交。"""

import contextlib
import hashlib
import json
import time

from .models import Message

INTAKE_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_events (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, received REAL NOT NULL,
 handled INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS inbound_pending ON inbound_events(handled, received);
"""


class DurableIntake:
    def capture(self, messages: list[Message], now: float) -> None:
        account = self.get_meta("account")
        with self.transaction() as db:
            pending = db.execute("SELECT count(*) FROM inbound_events WHERE handled=0").fetchone()[0]
            if pending + len(messages) > self.settings.event_buffer:
                raise BufferError("持久收件缓冲已满，暂不确认页面事件")
            db.executemany(
                "INSERT OR IGNORE INTO inbound_events(id,payload,received) VALUES (?,?,?)",
                [
                    (hashlib.sha256(f"{account}:{message.key}".encode()).hexdigest(), message.dump(), now)
                    for message in messages
                ],
            )

    def inbound(self) -> list[dict]:
        with contextlib.closing(self.connect()) as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM inbound_events WHERE handled=0 AND available<=? "
                    "ORDER BY available,received,rowid LIMIT 100",
                    (time.time(),),
                )
            ]

    def handle_inbound(self, event: dict, now: float) -> str:
        message = Message(**json.loads(event["payload"]))
        if message.kind == "selection":
            result = self.select_video(message, now)
        elif message.kind == "control":
            result = self.control(message, now)
        else:
            result = self.ingest(message, now)
        # ingest / selection 自身有持久去重；此处之前崩溃会安全地再应用一次。
        with self.transaction() as db:
            if result == "deferred":
                db.execute("UPDATE inbound_events SET available=? WHERE id=?", (now + 1, event["id"]))
            else:
                db.execute("UPDATE inbound_events SET handled=1 WHERE id=?", (event["id"],))
        return result
