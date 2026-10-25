"""同发送者的编号进度与取消命令，独立限频并共享有界发送队列。"""

import json
import re

CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS command_events (
 id TEXT PRIMARY KEY, sender TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS command_sender ON command_events(sender,created);
"""

STATES = {
    "queued": "等待处理",
    "processing": "正在准备",
    "blocked": "等待共享素材",
    "waiting": "等待选择",
    "ready": "等待回传",
    "sending": "正在回传",
    "sent": "已回传",
    "failed": "处理失败",
    "uncertain": "回传结果待核对",
    "rejected": "未接收",
    "cancelled": "已取消",
}


def parse_control(text: str) -> dict | None:
    match = re.fullmatch(r"(进度|取消)\s*#?\s*([a-fA-F0-9]{8})", text.strip())
    return {"action": match[1], "code": match[2].upper()} if match else None


class Controls:
    def command_allowed(self, db, message, now: float) -> bool:
        if db.execute("SELECT 1 FROM command_events WHERE id=?", (message.key,)).fetchone():
            return True
        count = db.execute(
            "SELECT count(*) FROM command_events WHERE sender=? AND created>?", (message.sender, now - 60)
        ).fetchone()[0]
        db.execute("INSERT INTO command_events VALUES (?,?,?)", (message.key, message.sender, now))
        return count < self.settings.commands_per_minute

    def control(self, message, now: float) -> str:
        floor = self.message_floor(message.conversation, now)
        if not floor <= message.created <= now + 60:
            return "old"
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM choice_events WHERE id=?", (message.key,)).fetchone():
                return "duplicate"
            db.execute("INSERT INTO choice_events VALUES (?,?)", (message.key, now))
            if not self.command_allowed(db, message, now):
                return "limited"
            row = db.execute(
                "SELECT j.* FROM video_choices c JOIN jobs j ON j.id=c.job "
                "WHERE c.code=? AND j.sender=? AND j.conversation=?",
                (message.data["code"], message.sender, message.conversation),
            ).fetchone()
            if not row:
                return "unmatched"
            state = row["state"]
            text = self._control_text(db, row, message.data["action"], now)
            self.control_notice(db, message, text, now)
            return state

    @staticmethod
    def _control_text(db, job, action: str, now: float) -> str:
        if action == "进度":
            return "这条素材：" + STATES.get(job["state"], "等待处理") + "。"
        if job["state"] in {"sending", "uncertain"}:
            return "这条素材已开始提交回传，需要等待或核对结果；已提交的内容无法撤回。"
        if job["state"] in {"sent", "failed", "rejected", "cancelled"}:
            return "这条素材：" + STATES[job["state"]] + "。"
        if job["state"] == "processing":
            db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job["id"],))
            return "已取消后续回传，正在执行的准备结束后会释放资源。"
        db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=?", (now, job["id"]))
        return "已取消这条素材的后续处理；已回传的内容保留。"

    def control_notice(self, db, message, text: str, now: float) -> None:
        pending = db.execute(
            "SELECT count(*) FROM jobs WHERE state IN "
            "('queued','processing','ready','sending','uncertain','waiting','blocked')"
        ).fetchone()[0]
        if pending >= self.settings.max_pending:
            return
        db.execute(
            "INSERT INTO jobs(id,conversation,sender,message_id,created,received,payload,state,available,updated) "
            "VALUES (?,?,?,?,?,?,?,'ready',?,?)",
            (
                message.key,
                message.conversation,
                message.sender,
                message.id,
                message.created,
                now,
                message.dump(),
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO parts(job,number,payload) VALUES (?,0,?)",
            (message.key, json.dumps({"text": text}, ensure_ascii=False)),
        )

    @staticmethod
    def finish_cancellation(db, job_id: str, now: float) -> bool:
        row = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row[0]:
            return False
        db.execute("UPDATE jobs SET state='cancelled',lease=NULL,token=NULL,updated=? WHERE id=?", (now, job_id))
        return True
