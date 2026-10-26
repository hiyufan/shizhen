"""SQLite WAL 队列：收件去重、配额、租约和发送前持久化。"""

from __future__ import annotations

import contextlib
import json
import math
import sqlite3
import time
import uuid
import weakref
from pathlib import Path

from .assets import ASSET_SCHEMA, SharedAssets
from .choices import CHOICE_SCHEMA, VideoChoices
from .controls import CONTROL_SCHEMA, Controls
from .follow_store import FOLLOW_SCHEMA, FollowStore
from .intake import INTAKE_SCHEMA, DurableIntake
from .models import Message, Settings
from .outbound import OUTBOUND_SCHEMA, OutboundStore, individual_parts
from .scheduler import SCHEDULER_SCHEMA, charge, pick

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, conversation TEXT NOT NULL, sender TEXT NOT NULL,
 message_id TEXT NOT NULL, created REAL NOT NULL, received REAL NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL, available REAL NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, lease REAL, token TEXT,
 artifact TEXT, error TEXT, updated REAL NOT NULL,
 UNIQUE(conversation, message_id)
);
CREATE INDEX IF NOT EXISTS job_claim ON jobs(state, available, received);
CREATE INDEX IF NOT EXISTS job_sender ON jobs(sender, received, state);
CREATE TABLE IF NOT EXISTS parts (
 job TEXT NOT NULL REFERENCES jobs(id), number INTEGER NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', receipt TEXT,
 PRIMARY KEY(job, number)
);
CREATE TABLE IF NOT EXISTS cursors (conversation TEXT PRIMARY KEY, checked REAL NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
 key TEXT PRIMARY KEY, manifest TEXT NOT NULL, expires REAL NOT NULL, bytes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS timings (
 job TEXT NOT NULL REFERENCES jobs(id), phase TEXT NOT NULL, seconds REAL NOT NULL,
 updated REAL NOT NULL, PRIMARY KEY(job, phase)
);
"""
PENDING = "('queued','processing','ready','sending','uncertain','waiting','blocked')"


class LeaseLost(RuntimeError):
    pass


class Store(VideoChoices, DurableIntake, SharedAssets, Controls, OutboundStore, FollowStore):
    def __init__(self, root: Path, settings: Settings):
        self.root, self.settings = root, settings
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.path = root / "queue.sqlite3"
        with contextlib.closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                SCHEMA
                + CHOICE_SCHEMA
                + INTAKE_SCHEMA
                + SCHEDULER_SCHEMA
                + ASSET_SCHEMA
                + CONTROL_SCHEMA
                + OUTBOUND_SCHEMA
                + FOLLOW_SCHEMA
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(video_choices)")}
            if "prompt_started" not in columns:
                db.execute("ALTER TABLE video_choices ADD COLUMN prompt_started REAL")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "blocked_on" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN blocked_on TEXT")
            if "cancel_requested" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
            columns = {row[1] for row in db.execute("PRAGMA table_info(inbound_events)")}
            if "available" not in columns:
                db.execute("ALTER TABLE inbound_events ADD COLUMN available REAL NOT NULL DEFAULT 0")
        self.path.chmod(0o600)
        # 各操作仍用短连接，另常驻一个空闲连接：WAL 库最后一个连接关闭时 SQLite 会完整 checkpoint
        # 并删掉 -wal，轮询全用短连接就成了每次都 checkpoint、重建 -wal，空闲时也持续写盘。
        # 常驻连接不开事务、不挡 checkpoint，只把它推迟到 WAL 自动阈值；FULL 同步的持久性不变。
        # 进程被强杀时提交留在 -wal 里，复制备份要连同 -wal 一起，或用 sqlite3 的 .backup。
        self._keeper = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._keeper.execute("SELECT 1 FROM meta LIMIT 1").fetchall()
        weakref.finalize(self, self._keeper.close)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextlib.contextmanager
    def transaction(self):
        with contextlib.closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def get_meta(self, key: str, default: str = "") -> str:
        with contextlib.closing(self.connect()) as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        # 值没变就不改页：状态类字段每秒写一次，空提交不落 WAL、不触发同步。
        with self.transaction() as db:
            db.execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                "WHERE value IS NOT excluded.value",
                (key, value),
            )

    def message_floor(self, conversation: str, default: float = 0) -> float:
        with contextlib.closing(self.connect()) as db:
            key = "start_at:" + conversation
            values = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('start_at',?)", (key,)))
            return float(values.get(key, values.get("start_at", default)))

    def bind(self, account: str, now: float) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT value FROM meta WHERE key='account'").fetchone()
            if row and row[0] != account:
                raise RuntimeError("登录账号已变化，停止收发")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('account',?)", (account,))
            db.execute("INSERT OR IGNORE INTO meta VALUES ('start_at',?)", (str(now),))

    def configure_scope(self, mode: str, now: float) -> None:
        with self.transaction() as db:
            old = db.execute("SELECT value FROM meta WHERE key='scope'").fetchone()
            if old and old[0] == "pilot" and mode == "all":
                self._preserve_pilot_floors(db)
                # 新会话从开放时接收，已有会话保留基线及尚未应用的请求 / 选择。
                db.execute("UPDATE meta SET value=? WHERE key='start_at'", (str(now),))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('scope',?)", (mode,))

    @staticmethod
    def _preserve_pilot_floors(db) -> None:
        baseline = db.execute("SELECT value FROM meta WHERE key='start_at'").fetchone()
        scope = db.execute("SELECT value FROM meta WHERE key='pilot_scope'").fetchone()
        for conversation in json.loads(scope[0] if scope else "[]"):
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", ("start_at:" + conversation, baseline[0]))

    def _rejection(self, db, message: Message, now: float) -> str | None:
        pending = db.execute(f"SELECT count(*) FROM jobs WHERE state IN {PENDING}").fetchone()[0]
        sender = db.execute(
            f"SELECT count(*) FROM jobs WHERE sender=? AND state IN {PENDING}", (message.sender,)
        ).fetchone()[0]
        hourly = db.execute(
            "SELECT count(*) FROM jobs WHERE sender=? AND received>? AND state!='rejected' "
            "AND json_extract(payload,'$.kind') NOT IN ('selection','control')",
            (message.sender, now - 3600),
        ).fetchone()[0]
        reserve = min(self.settings.control_reserve, self.settings.max_pending // 10)
        if pending >= self.settings.max_pending - reserve:
            return "queue_full"
        backlog = db.execute(
            "SELECT coalesce(sum(CASE WHEN json_extract(p.payload,'$.live_pair')=1 THEN 1 "
            "ELSE max(1,coalesce(json_array_length(p.payload,'$.files'),1)) END),0) "
            "FROM parts p JOIN jobs j ON j.id=p.job "
            "WHERE j.state IN ('ready','sending') AND p.state!='sent'"
        ).fetchone()[0]
        if backlog * self.settings.send_interval >= self.settings.max_queue_seconds:
            return "busy"
        protected = db.execute(
            "SELECT coalesce(sum(bytes),0) FROM artifacts WHERE key IN "
            f"(SELECT artifact FROM jobs WHERE state IN {PENDING} UNION "
            "SELECT c.artifact FROM video_choices c JOIN jobs j ON j.id=c.job "
            f"WHERE j.state IN {PENDING})"
        ).fetchone()[0]
        if protected + self.settings.job_bytes * self.settings.workers > self.settings.disk_bytes:
            return "busy"
        if sender >= self.settings.sender_pending or hourly >= self.settings.sender_hourly:
            return "sender_limit"
        return None

    def ingest(self, message: Message, now: float | None = None) -> str:
        now = time.time() if now is None else now
        floor = self.message_floor(message.conversation, now)
        if message.created < floor or message.created > now + 60:
            return "old"
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM jobs WHERE id=?", (message.key,)).fetchone():
                return "duplicate"
            reason = self._rejection(db, message, now)
            notice = (
                reason in {"sender_limit", "busy"}
                and not db.execute(
                    "SELECT 1 FROM jobs WHERE sender=? AND error IN ('reject:sender_limit','reject:busy') "
                    "AND received>? LIMIT 1",
                    (message.sender, now - 300),
                ).fetchone()
            )
            state = "ready" if notice else ("rejected" if reason else "queued")
            error = "reject:" + reason if notice else reason
            db.execute(
                "INSERT INTO jobs(id,conversation,sender,message_id,created,received,"
                "payload,state,available,error,updated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    message.key,
                    message.conversation,
                    message.sender,
                    message.id,
                    message.created,
                    now,
                    message.dump(),
                    state,
                    now,
                    error,
                    now,
                ),
            )
            self._measure(db, message.key, "receive_lag", max(0, now - message.created), now)
            if notice:
                text = (
                    "当前排队较多，请稍后再发；已经接收的内容会继续处理。"
                    if reason == "busy"
                    else "转发得有点快，请稍后再发；已经排队的内容会继续处理。"
                )
                payload = {"text": text}
                db.execute(
                    "INSERT INTO parts(job,number,payload) VALUES (?,?,?)",
                    (message.key, 0, json.dumps(payload, ensure_ascii=False)),
                )
            return state

    def cursor(self, conversation: str) -> float:
        with contextlib.closing(self.connect()) as db:
            row = db.execute("SELECT checked FROM cursors WHERE conversation=?", (conversation,)).fetchone()
        return row[0] if row else self.message_floor(conversation)

    def checkpoint(self, conversation: str, checked: float) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?)", (conversation, checked))

    def claim(self, state: str, now: float, scope: set[str] | None = None) -> dict | None:
        target = {"queued": "processing", "ready": "sending"}[state]
        with self.transaction() as db:
            if state == "ready" and self.settings.native_pipeline and not self.outbound_gate(db, now):
                return None
            row = pick(db, state, now, scope, self.settings.send_interval, self.settings.native_pipeline)
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute(
                "UPDATE jobs SET state=?,token=?,lease=?,attempts=attempts+?,updated=? WHERE id=?",
                (target, token, now + self.settings.lease_seconds, int(state == "queued"), now, row["id"]),
            )
            phase = "prepare_wait" if state == "queued" else "dispatch_queue"
            self._measure(db, row["id"], phase, max(0, now - row["updated"]), now)
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def charge_dispatch(self, job: dict, payload: dict, elapsed: float) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            charge(db, job["sender"], payload, elapsed, self.settings.send_interval)

    @staticmethod
    def _measure(db, job: str, phase: str, seconds: float, now: float) -> None:
        db.execute(
            "INSERT INTO timings VALUES (?,?,?,?) ON CONFLICT(job,phase) DO UPDATE SET "
            "seconds=seconds+excluded.seconds,updated=excluded.updated",
            (job, phase, seconds, now),
        )

    def measure(self, job: dict, phase: str, seconds: float) -> None:
        with self.transaction() as db:
            self._measure(db, job["id"], phase, max(0, seconds), time.time())

    @staticmethod
    def _owned(db, job: dict) -> None:
        row = db.execute("SELECT token FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if not row or row[0] != job["token"]:
            raise LeaseLost("任务租约已变化")

    def ready(self, job: dict, artifact: str, parts: list[dict], now: float) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            if self.finish_cancellation(db, job["id"], now):
                return
            if len(parts) == 1 and "offer_video" in parts[0]:
                parts = self._offer_video(db, job, artifact, parts[0]["offer_video"])
            if self.settings.native_pipeline:
                parts = individual_parts(parts)
            start = self._next_part(db, job["id"])
            db.executemany(
                "INSERT INTO parts(job,number,payload) VALUES (?,?,?)",
                [(job["id"], start + index, json.dumps(part, ensure_ascii=False)) for index, part in enumerate(parts)],
            )
            db.execute(
                "UPDATE jobs SET state='ready',artifact=?,error=NULL,lease=NULL,token=NULL,updated=? WHERE id=?",
                (artifact, now, job["id"]),
            )

    @staticmethod
    def _next_part(db, job_id: str) -> int:
        return db.execute("SELECT coalesce(max(number)+1,0) FROM parts WHERE job=?", (job_id,)).fetchone()[0]

    def pin_artifact(self, job: dict, key: str) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            db.execute("UPDATE jobs SET artifact=? WHERE id=?", (key, job["id"]))

    def preparation_failed(self, job: dict, reason: str, now: float, retryable: bool = True) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            if self.finish_cancellation(db, job["id"], now):
                return
            terminal = not retryable or job["attempts"] >= self.settings.max_attempts
            state = "ready" if terminal else "queued"
            if terminal:
                payload = {"text": "这条内容暂时没能处理成功，可以稍后重新转发，或换一条试试。"}
                db.execute(
                    "INSERT INTO parts(job,number,payload) VALUES (?,?,?)",
                    (job["id"], self._next_part(db, job["id"]), json.dumps(payload, ensure_ascii=False)),
                )
            db.execute(
                "UPDATE jobs SET state=?,available=?,error=?,token=NULL,lease=NULL,updated=? WHERE id=?",
                (state, now + 15 * 2 ** job["attempts"], reason, now, job["id"]),
            )

    def parts(self, job: dict) -> list[dict]:
        with contextlib.closing(self.connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM parts WHERE job=? ORDER BY number", (job["id"],))]

    def start_part(self, job: dict, number: int, now: float) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            changed = db.execute(
                "UPDATE parts SET state='sending' WHERE job=? AND number=? AND state='pending'",
                (job["id"], number),
            ).rowcount
            if changed != 1:
                raise LeaseLost("该发送片段已有记录")
            db.execute("UPDATE jobs SET updated=? WHERE id=?", (now, job["id"]))
            db.execute(
                "UPDATE video_choices SET prompt_started=coalesce(prompt_started,?) WHERE job=? AND mode IS NULL",
                (now, job["id"]),
            )

    def finish_part(self, job: dict, number: int, receipt: list[str]) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            db.execute(
                "UPDATE parts SET state='sent',receipt=? WHERE job=? AND number=? AND state='sending'",
                (json.dumps(receipt), job["id"], number),
            )

    @staticmethod
    def _completed_state(error: str | None) -> str:
        if not error or error == "choice_hint":
            return "sent"
        return "rejected" if error.startswith("reject:") else "failed"

    def finish(self, job: dict, state: str, now: float) -> None:
        if state not in ("sent", "uncertain", "ready"):
            raise ValueError("不支持的发送状态")
        with self.transaction() as db:
            self._owned(db, job)
            if state == "sent":
                state = self.after_delivery(db, job["id"], job.get("error"), now)
            db.execute("UPDATE jobs SET state=?,token=NULL,lease=NULL,updated=? WHERE id=?", (state, now, job["id"]))
            if state in {"sent", "failed", "rejected"}:
                self._measure_total(db, job["id"], now)

    @staticmethod
    def _measure_total(db, job_id: str, now: float) -> None:
        received = db.execute("SELECT received FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        choice = db.execute("SELECT seconds FROM timings WHERE job=? AND phase='choice_wait'", (job_id,)).fetchone()
        total = max(0, now - received)
        for phase, seconds in [("total", total), ("service_total", max(0, total - (choice[0] if choice else 0)))]:
            db.execute(
                "INSERT INTO timings VALUES (?,?,?,?) ON CONFLICT(job,phase) DO UPDATE "
                "SET seconds=excluded.seconds,updated=excluded.updated",
                (job_id, phase, seconds, now),
            )

    def resolve(self, job_id: str, decision: str, now: float) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT state,error FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row[0] != "uncertain":
                raise ValueError("只能核对 uncertain 状态的任务")
            if decision == "delivered":
                db.execute(
                    "UPDATE parts SET state='sent',receipt='[\"operator_confirmed\"]' WHERE job=? AND state='sending'",
                    (job_id,),
                )
                pending = db.execute(
                    "SELECT count(*) FROM parts WHERE job=? AND state='pending'", (job_id,)
                ).fetchone()[0]
                state = "ready" if pending else self.after_delivery(db, job_id, row["error"], now)
            elif decision == "abandon":
                state = "failed"
            else:
                raise ValueError("只支持 delivered 或 abandon")
            db.execute("UPDATE jobs SET state=?,updated=? WHERE id=?", (state, now, job_id))
            db.execute(
                "UPDATE outbound SET state=CASE WHEN ?='failed' THEN 'abandoned' ELSE 'acked' END "
                "WHERE job=? AND state='submitted'",
                (state, job_id),
            )

    def recover(self, now: float) -> None:
        with self.transaction() as db:
            self.recover_outbound(db, now)
            db.execute(
                "UPDATE jobs SET state='cancelled',token=NULL,lease=NULL "
                "WHERE cancel_requested=1 AND state IN ('queued','processing','blocked')"
            )
            db.execute("UPDATE jobs SET state='queued',token=NULL,lease=NULL WHERE state='processing'")
            db.execute("UPDATE jobs SET state='queued',blocked_on=NULL WHERE state='blocked'")
            db.execute("DELETE FROM asset_tasks")
            for row in db.execute("SELECT id,error FROM jobs WHERE state='sending'").fetchall():
                states = [r[0] for r in db.execute("SELECT state FROM parts WHERE job=?", (row[0],))]
                state = "uncertain" if "sending" in states else "ready"
                if states and all(s == "sent" for s in states):
                    state = self.after_delivery(db, row["id"], row["error"], now)
                db.execute("UPDATE jobs SET state=?,token=NULL,lease=NULL,updated=? WHERE id=?", (state, now, row[0]))
            if db.execute("SELECT 1 FROM jobs WHERE state='uncertain' LIMIT 1").fetchone():
                db.execute(
                    "INSERT INTO meta VALUES ('paused','delivery_requires_review') ON CONFLICT(key) "
                    "DO UPDATE SET value=excluded.value WHERE value=''"
                )

    def artifact(self, key: str, now: float) -> dict | None:
        with contextlib.closing(self.connect()) as db:
            row = db.execute("SELECT manifest FROM artifacts WHERE key=? AND expires>?", (key, now)).fetchone()
            return json.loads(row[0]) if row else None

    def artifact_keys(self) -> set[str]:
        with contextlib.closing(self.connect()) as db:
            return {row[0] for row in db.execute("SELECT key FROM artifacts")}

    def cache(self, key: str, manifest: dict, size: int, now: float) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?)",
                (key, json.dumps(manifest), now + self.settings.cache_seconds, size),
            )

    def expired_artifacts(self, now: float, pressure: bool = False) -> list[str]:
        with contextlib.closing(self.connect()) as db:
            return [
                row[0]
                for row in db.execute(
                    f"SELECT key FROM artifacts WHERE (expires<? OR ?) AND key NOT IN "
                    f"(SELECT artifact FROM jobs WHERE state IN {PENDING} AND artifact IS NOT NULL) "
                    "AND key NOT IN (SELECT c.artifact FROM video_choices c JOIN jobs j ON j.id=c.job "
                    f"WHERE j.state IN {PENDING}) ORDER BY expires",
                    (now, pressure),
                )
            ]

    def remove_artifact(self, key: str) -> bool:
        with self.transaction() as db:
            direct = db.execute(
                f"SELECT 1 FROM jobs WHERE artifact=? AND state IN {PENDING} LIMIT 1", (key,)
            ).fetchone()
            chosen = db.execute(
                "SELECT 1 FROM video_choices c JOIN jobs j ON j.id=c.job "
                f"WHERE c.artifact=? AND j.state IN {PENDING} LIMIT 1",
                (key,),
            ).fetchone()
            if direct or chosen:
                return False
            db.execute("DELETE FROM artifacts WHERE key=?", (key,))
            return True

    def prune(self, now: float) -> None:
        # 去重元数据保留三十天；文件缓存单独清理，不因删文件而丢失已处理消息的记录。
        with self.transaction() as db:
            cutoff = now - 30 * 86400
            db.execute("DELETE FROM choice_events WHERE created<?", (cutoff,))
            db.execute("DELETE FROM command_events WHERE created<?", (now - 86400,))
            db.execute("DELETE FROM inbound_events WHERE handled=1 AND received<?", (cutoff,))
            db.execute(
                "DELETE FROM video_choices WHERE job IN (SELECT id FROM jobs WHERE state IN "
                "('sent','failed','rejected','cancelled') AND updated<?)",
                (cutoff,),
            )
            db.execute(
                "DELETE FROM timings WHERE job IN (SELECT id FROM jobs WHERE state IN "
                "('sent','failed','rejected','cancelled') AND updated<?)",
                (cutoff,),
            )
            db.execute(
                "DELETE FROM parts WHERE job IN (SELECT id FROM jobs WHERE state IN "
                "('sent','failed','rejected','cancelled') AND updated<?)",
                (cutoff,),
            )
            db.execute(
                "DELETE FROM jobs WHERE state IN ('sent','failed','rejected','cancelled') AND updated<?", (cutoff,)
            )
            db.execute("DELETE FROM scheduler WHERE sender NOT IN (SELECT sender FROM jobs)")

    def list_jobs(self, state: str | None = None) -> list[dict]:
        with contextlib.closing(self.connect()) as db:
            rows = db.execute(
                "SELECT id,state,attempts,received,updated,error FROM jobs WHERE (? IS NULL OR state=?) "
                "ORDER BY received DESC LIMIT 50",
                (state, state),
            )
            return [dict(row) for row in rows]

    def status(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with contextlib.closing(self.connect()) as db:
            db.execute("BEGIN")
            counts = dict(db.execute("SELECT state,count(*) FROM jobs GROUP BY state"))
            oldest = db.execute(f"SELECT min(received) FROM jobs WHERE state IN {PENDING}").fetchone()[0]
            cache_bytes = db.execute("SELECT coalesce(sum(bytes),0) FROM artifacts").fetchone()[0]
            timings = {
                row[0]: {"samples": row[1], "average_seconds": round(row[2], 2), "max_seconds": round(row[3], 2)}
                for row in db.execute(
                    "SELECT phase,count(*),avg(seconds),max(seconds) FROM timings WHERE job IN "
                    "(SELECT id FROM jobs ORDER BY received DESC LIMIT 100) GROUP BY phase"
                )
            }
            for phase, values in timings.items():
                samples = sorted(
                    row[0]
                    for row in db.execute(
                        "SELECT seconds FROM timings WHERE phase=? AND job IN "
                        "(SELECT id FROM jobs ORDER BY received DESC LIMIT 100)",
                        (phase,),
                    )
                )
                values["p50_seconds"] = round(samples[math.ceil(len(samples) * 0.5) - 1], 2)
                values["p95_seconds"] = round(samples[math.ceil(len(samples) * 0.95) - 1], 2)
            runnable = db.execute(
                "SELECT min(updated) FROM jobs WHERE state IN ('queued','ready') AND available<=?", (now,)
            ).fetchone()[0]
            incoming = db.execute("SELECT count(*) FROM inbound_events WHERE handled=0").fetchone()[0]
        heartbeat = float(self.get_meta("heartbeat", "0"))
        return {
            "counts": counts,
            "oldest_pending_seconds": max(0, now - oldest) if oldest else 0,
            "oldest_runnable_seconds": max(0, now - runnable) if runnable else 0,
            "pending_inbound": incoming,
            "event_mode": self.get_meta("event_mode", "starting"),
            "last_event_receive": self.get_meta("last_event_receive"),
            "cache_bytes": cache_bytes,
            "timings": timings,
            "paused": self.get_meta("paused"),
            "last_receive": self.get_meta("last_receive"),
            "last_send": self.get_meta("last_send"),
            "heartbeat": self.get_meta("heartbeat"),
            "heartbeat_age_seconds": max(0, now - heartbeat) if heartbeat else None,
            "scope": self.get_meta("scope"),
            "sending_enabled": self.get_meta("sending_enabled"),
            "catchup_required": self.get_meta("catchup_required") or self.get_meta("event_gap"),
            "max_pending": self.settings.max_pending,
            "workers": self.settings.workers,
            "send_interval": self.settings.send_interval,
            "outbound_mode": self.get_meta("outbound_mode", "legacy"),
            "outbound": self.outbound_status(),
            "follows": self.follow_status(),
        }
