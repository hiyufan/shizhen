"""持久上传计划、发送意图与节奏预算；页面资源只保存不含凭证的句柄。"""

from __future__ import annotations

import contextlib
import json
import uuid

OUTBOUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound (
 job TEXT NOT NULL, number INTEGER NOT NULL, client_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL, bytes INTEGER NOT NULL DEFAULT 0,
 handle TEXT, generation TEXT, token TEXT, lease REAL, available REAL NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
 server_id TEXT UNIQUE, PRIMARY KEY(job,number),
 FOREIGN KEY(job,number) REFERENCES parts(job,number) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS outbound_metrics (
 job TEXT NOT NULL, number INTEGER NOT NULL, phase TEXT NOT NULL,
 value REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY(job,number,phase),
 FOREIGN KEY(job,number) REFERENCES outbound(job,number) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS outbound_claim ON outbound(state,available,updated);
"""


def individual_parts(parts: list[dict]) -> list[dict]:
    result = []
    for part in parts:
        if part.get("live_pair") or len(part.get("files", [])) < 2:
            result.append(part)
            continue
        for name, digest in zip(part["files"], part["md5"], strict=True):
            item = part | {"files": [name], "md5": [digest]}
            item.pop("client_id", None)
            result.append(item)
    return result


class OutboundStore:
    def unplanned_outbound(self, scope: set[str]) -> list[dict]:
        with self.transaction() as db:
            self._split_legacy_batches(db)
            rows = db.execute(
                "SELECT j.id job,j.conversation,j.sender,p.number,p.payload FROM parts p JOIN jobs j ON j.id=p.job "
                "LEFT JOIN outbound o ON o.job=p.job AND o.number=p.number "
                "WHERE j.state='ready' AND p.state='pending' AND o.job IS NULL ORDER BY j.received,p.number LIMIT 100"
            )
            return [dict(row) for row in rows if not scope or row["conversation"] in scope]

    @staticmethod
    def _split_legacy_batches(db) -> None:
        jobs = db.execute(
            "SELECT DISTINCT j.id FROM jobs j JOIN parts p ON p.job=j.id WHERE j.state='ready' "
            "AND p.state='pending' AND json_array_length(p.payload,'$.files')>1 "
            "AND coalesce(json_extract(p.payload,'$.live_pair'),0)=0 "
            "AND NOT EXISTS(SELECT 1 FROM outbound o WHERE o.job=j.id)"
        ).fetchall()
        for job in jobs:
            pending = db.execute(
                "SELECT payload FROM parts WHERE job=? AND state='pending' ORDER BY number", job
            ).fetchall()
            start = db.execute("SELECT min(number) FROM parts WHERE job=? AND state='pending'", job).fetchone()[0]
            parts = individual_parts([json.loads(row[0]) for row in pending])
            db.execute("DELETE FROM parts WHERE job=? AND state='pending'", job)
            db.executemany(
                "INSERT INTO parts(job,number,payload) VALUES (?,?,?)",
                [(job[0], start + i, json.dumps(part)) for i, part in enumerate(parts)],
            )

    def plan_outbound(self, row: dict, size: int, now: float) -> None:
        payload = json.loads(row["payload"])
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO outbound(job,number,client_id,state,bytes,available,updated) "
                "SELECT ?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM jobs WHERE id=? AND state='ready')",
                (
                    row["job"],
                    row["number"],
                    str(uuid.uuid4()),
                    "queued" if payload.get("files") else "uploaded",
                    size,
                    now,
                    now,
                    row["job"],
                ),
            )

    def claim_upload(self, now: float, scope: set[str]) -> dict | None:
        with self.transaction() as db:
            used = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM outbound WHERE state='uploading'").fetchone()
            ahead = db.execute(
                "SELECT count(*) FROM outbound o JOIN jobs j ON j.id=o.job "
                "WHERE o.state IN ('uploading','uploaded') AND o.bytes>0 AND j.state IN ('ready','sending')"
            ).fetchone()[0]
            if (
                used[0] >= self.settings.upload_workers
                or ahead >= self.settings.upload_workers + self.settings.ack_window
            ):
                return None
            rows = db.execute(
                "SELECT o.*,j.sender,j.conversation,p.payload FROM outbound o JOIN jobs j ON j.id=o.job "
                "JOIN parts p ON p.job=o.job AND p.number=o.number "
                "LEFT JOIN scheduler s ON s.phase='upload' AND s.sender=j.sender "
                "WHERE o.state='queued' AND o.available<=? AND j.state IN ('ready','sending') "
                "AND p.state='pending' AND o.bytes+?<=? "
                "ORDER BY coalesce(s.turn,0),j.received,o.number",
                (now, used[1], self.settings.upload_bytes),
            )
            row = next((dict(row) for row in rows if not scope or row["conversation"] in scope), None)
            if not row:
                return None
            clock = db.execute("SELECT coalesce(max(turn),0)+1 FROM scheduler WHERE phase='upload'").fetchone()[0]
            db.execute(
                "INSERT INTO scheduler VALUES ('upload',?,0,?) "
                "ON CONFLICT(phase,sender) DO UPDATE SET turn=excluded.turn",
                (row["sender"], clock),
            )
            token = uuid.uuid4().hex
            db.execute(
                "UPDATE outbound SET state='uploading',token=?,lease=?,attempts=attempts+1,updated=? "
                "WHERE job=? AND number=?",
                (token, now + self.settings.lease_seconds, now, row["job"], row["number"]),
            )
            return row | {"token": token, "attempts": row["attempts"] + 1}

    def uploaded(self, row: dict, result: dict, now: float) -> bool:
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE outbound SET state='uploaded',handle=?,generation=?,token=NULL,lease=NULL,attempts=0,updated=? "
                "WHERE job=? AND number=? AND state='uploading' AND token=? "
                "AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=outbound.job AND j.state IN ('ready','sending'))",
                (result["handle"], result["generation"], now, row["job"], row["number"], row["token"]),
            ).rowcount
            return bool(changed)

    def upload_failed(self, row: dict, now: float) -> None:
        with self.transaction() as db:
            terminal = row["attempts"] >= self.settings.max_attempts
            changed = db.execute(
                "UPDATE outbound SET state=?,token=NULL,lease=NULL,available=?,updated=? "
                "WHERE job=? AND number=? AND token=?",
                (
                    "failed" if terminal else "queued",
                    now + 5 * 2 ** row["attempts"],
                    now,
                    row["job"],
                    row["number"],
                    row["token"],
                ),
            ).rowcount
            if terminal and changed:
                self._upload_failure_notice(db, row, now)

    @staticmethod
    def _upload_failure_notice(db, row: dict, now: float) -> None:
        # 只替换未提交的片段；正在提交的其他片段必须继续核对，不改写发送意图。
        pending = db.execute(
            "SELECT number FROM parts WHERE job=? AND state='pending' ORDER BY number", (row["job"],)
        ).fetchall()
        if not pending:
            return
        db.executemany("DELETE FROM parts WHERE job=? AND number=?", [(row["job"], p[0]) for p in pending])
        notice = {"text": "这条素材上传暂时失败，已回传的内容保留；可以稍后重新转发。"}
        db.execute(
            "INSERT INTO parts(job,number,payload) VALUES (?,?,?)",
            (row["job"], pending[0][0], json.dumps(notice, ensure_ascii=False)),
        )
        db.execute("UPDATE jobs SET error='upload_failed',updated=? WHERE id=?", (now, row["job"]))

    def retire_outbound(self) -> list[dict]:
        with self.transaction() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT o.* FROM outbound o JOIN jobs j ON j.id=o.job "
                    "WHERE o.state IN ('queued','uploading','uploaded') AND j.state NOT IN ('ready','sending')"
                )
            ]
            db.executemany(
                "UPDATE outbound SET state='abandoned',token=NULL,lease=NULL WHERE job=? AND number=?",
                [(r["job"], r["number"]) for r in rows],
            )
            return rows

    def sync_outbound_clock(self, next_submit: float, cooldown: float) -> None:
        with self.transaction() as db:
            for key, value in (("next_send_at", next_submit), ("send_cooldown", cooldown)):
                old = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                db.execute(
                    "INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(max(value, float(old[0]) if old else 0)))
                )

    def real_work_pending(self) -> bool:
        """有正式准备或上传在排队 / 进行时，预热让路。"""
        with contextlib.closing(self.connect()) as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM outbound WHERE state IN ('queued','uploading') "
                    "UNION ALL SELECT 1 FROM jobs WHERE state IN ('queued','processing') LIMIT 1"
                ).fetchone()
            )

    def outbound_status(self) -> dict:
        with contextlib.closing(self.connect()) as db:
            counts = dict(db.execute("SELECT state,count(*) FROM outbound GROUP BY state"))
            active = db.execute("SELECT coalesce(sum(bytes),0) FROM outbound WHERE state='uploading'").fetchone()[0]
            attempts = dict(
                db.execute(
                    "SELECT phase,sum(value) FROM outbound_metrics "
                    "WHERE phase IN ('attempts','retry_blocked','event_receipt')"
                    " AND updated>strftime('%s','now')-86400 GROUP BY phase"
                )
            )
            return {"states": counts, "uploading_bytes": active, "recent_counters": attempts}

    def outbound_measure(self, row: dict, values: dict, now: float) -> None:
        with self.transaction() as db:
            db.executemany(
                "INSERT INTO outbound_metrics VALUES (?,?,?,?,?) ON CONFLICT(job,number,phase) DO UPDATE "
                "SET value=excluded.value,updated=excluded.updated",
                [(row["job"], row["number"], key, max(0, value), now) for key, value in values.items()],
            )

    def fail_outbound_plan(self, row: dict, now: float) -> None:
        with self.transaction() as db:
            self._upload_failure_notice(db, row, now)

    def outbound_part(self, job: dict, number: int) -> dict | None:
        with contextlib.closing(self.connect()) as db:
            row = db.execute("SELECT * FROM outbound WHERE job=? AND number=?", (job["id"], number)).fetchone()
            return dict(row) if row else None

    def invalidate_upload(self, row: dict, now: float) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE outbound SET state='queued',handle=NULL,generation=NULL,attempts=0,available=?,updated=? "
                "WHERE job=? AND number=? AND state='uploaded'",
                (now, now, row["job"], row["number"]),
            )

    def begin_outbound(self, job: dict, row: dict, now: float) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            paused = db.execute("SELECT value FROM meta WHERE key='paused'").fetchone()
            if (paused and paused[0]) or not self.outbound_gate(db, now):
                raise RuntimeError("发送预算或暂停状态已变化")
            changed = db.execute(
                "UPDATE outbound SET state='submitted',updated=? WHERE job=? AND number=? AND state='uploaded'",
                (now, job["id"], row["number"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("上传计划未就绪")
            db.execute("UPDATE parts SET state='sending' WHERE job=? AND number=?", (job["id"], row["number"]))
            db.execute(
                "UPDATE video_choices SET prompt_started=coalesce(prompt_started,?) WHERE job=? AND mode IS NULL",
                (now, job["id"]),
            )
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_attempt',?)", (str(now),))
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('next_send_at',?)", (str(now + self.settings.send_interval),)
            )

    def acknowledge_outbound(self, job: dict, row: dict, server_id: str, now: float) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            db.execute(
                "UPDATE outbound SET state='acked',server_id=?,updated=? "
                "WHERE job=? AND number=? AND state='submitted'",
                (server_id, now, job["id"], row["number"]),
            )
            db.execute(
                "UPDATE parts SET state='sent',receipt=? WHERE job=? AND number=? AND state='sending'",
                (json.dumps([server_id]), job["id"], row["number"]),
            )

    def reject_outbound(self, job: dict, number: int, server_id: str, code: int, now: float) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            changed = db.execute(
                "UPDATE outbound SET state='rejected',server_id=?,updated=? "
                "WHERE job=? AND number=? AND state='submitted'",
                (server_id, now, job["id"], number),
            ).rowcount
            if changed != 1:
                raise RuntimeError("受限回执不属于当前发送意图")
            db.execute(
                "UPDATE parts SET state='rejected',receipt=? WHERE job=? AND number=? AND state='sending'",
                (json.dumps({"server_id": server_id, "delivery_restricted": True, "code": code}), job["id"], number),
            )
            db.execute(
                "UPDATE jobs SET state='failed',error=?,token=NULL,lease=NULL,updated=? WHERE id=?",
                (f"platform_delivery_restricted:{code}", now, job["id"]),
            )

    def outbound_gate(self, db, now: float) -> bool:
        deadline = db.execute("SELECT value FROM meta WHERE key='next_send_at'").fetchone()
        cooldown = db.execute("SELECT value FROM meta WHERE key='send_cooldown'").fetchone()
        active = db.execute("SELECT count(*) FROM outbound WHERE state='submitted'").fetchone()[0]
        return active < self.settings.ack_window and now >= max(
            float(deadline[0]) if deadline else 0, float(cooldown[0]) if cooldown else 0
        )

    @staticmethod
    def recover_outbound(db, now: float) -> None:
        db.execute(
            "UPDATE outbound SET state='queued',handle=NULL,generation=NULL,token=NULL,lease=NULL,available=? "
            "WHERE state IN ('uploading','uploaded') AND handle IS NOT NULL",
            (now,),
        )
        db.execute("UPDATE outbound SET state='queued',token=NULL,lease=NULL WHERE state='uploading'")
