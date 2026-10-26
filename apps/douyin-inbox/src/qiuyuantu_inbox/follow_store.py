"""粉丝基线、分页进度与回关意图；提交结果未知时只核对，不重复关注。"""

import contextlib
import json
import uuid

FOLLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS follow_users (
 uid TEXT PRIMARY KEY, sec_uid TEXT NOT NULL, state TEXT NOT NULL,
 discovered REAL NOT NULL, updated REAL NOT NULL, token TEXT, submitted REAL, error TEXT
);
CREATE INDEX IF NOT EXISTS follow_pending ON follow_users(state,discovered);
"""


class FollowStore:
    def follow_scan(self, now: float) -> dict:
        saved = self.get_meta("follow_scan")
        return json.loads(saved) if saved else {"ceiling": int(now), "cursor": int(now)}

    def record_follow_page(self, page: dict, now: float, scan: dict | None = None) -> None:
        with self.transaction() as db:
            baseline = db.execute("SELECT value FROM meta WHERE key='follow_baseline'").fetchone()
            users = {user["uid"]: user for user in page["users"]}
            new = [
                u
                for u in users.values()
                if not db.execute("SELECT 1 FROM follow_users WHERE uid=?", (u["uid"],)).fetchone()
            ]
            pending = db.execute("SELECT count(*) FROM follow_users WHERE state IN ('queued','checking')").fetchone()[0]
            eligible = [u for u in new if baseline and u["follower"] == 1 and u["following"] == 0 and not u["blocked"]]
            if pending + len(eligible) > self.settings.follow_pending:
                raise BufferError("回关队列已满，保留分页进度")
            for user in new:
                state = "queued" if user in eligible else "baseline" if not baseline else "skipped"
                db.execute(
                    "INSERT INTO follow_users(uid,sec_uid,state,discovered,updated) VALUES (?,?,?,?,?)",
                    (user["uid"], user["sec_uid"], state, now, now),
                )
            if scan is not None:
                self._follow_checkpoint(db, page, scan, now)

    @staticmethod
    def _follow_checkpoint(db, page: dict, scan: dict, now: float) -> None:
        saved = db.execute("SELECT value FROM meta WHERE key='follow_scan'").fetchone()
        if saved and json.loads(saved[0]) != scan:
            raise RuntimeError("回关扫描游标已变化")
        if page["more"]:
            scan = scan | {"cursor": page["cursor"]}
            db.execute("INSERT OR REPLACE INTO meta VALUES ('follow_scan',?)", (json.dumps(scan),))
        else:
            db.execute("DELETE FROM meta WHERE key='follow_scan'")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('follow_baseline',?)", (str(now),))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('follow_last_scan',?)", (str(now),))

    def claim_follow(self, now: float) -> dict | None:
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='follow_paused' AND value<>''").fetchone():
                return None
            row = db.execute(
                "SELECT * FROM follow_users WHERE state='queued' ORDER BY discovered,uid LIMIT 1"
            ).fetchone()
            if not row or not self._follow_budget(db, now):
                return None
            token = uuid.uuid4().hex
            db.execute(
                "UPDATE follow_users SET state='checking',token=?,updated=? WHERE uid=?", (token, now, row["uid"])
            )
            return dict(row) | {"token": token}

    def _follow_budget(self, db, now: float) -> bool:
        last, hourly, daily = db.execute(
            "SELECT max(submitted),sum(submitted>?),sum(submitted>?) FROM follow_users",
            (now - 3600, now - 86400),
        ).fetchone()
        return (
            (last is None or now - last >= self.settings.follow_interval)
            and (hourly or 0) < self.settings.follow_hourly
            and (daily or 0) < self.settings.follow_daily
        )

    def begin_follow(self, job: dict, now: float) -> bool:
        with self.transaction() as db:
            paused = db.execute("SELECT 1 FROM meta WHERE key IN ('paused','follow_paused') AND value<>''").fetchone()
            if paused or not self._follow_budget(db, now):
                db.execute(
                    "UPDATE follow_users SET state='queued',token=NULL WHERE uid=? AND state='checking' AND token=?",
                    (job["uid"], job["token"]),
                )
                return False
            changed = db.execute(
                "UPDATE follow_users SET state='submitted',submitted=?,updated=? "
                "WHERE uid=? AND state='checking' AND token=?",
                (now, now, job["uid"], job["token"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("回关任务所有权已变化")
            return True

    def defer_follow(self, job: dict) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE follow_users SET state='queued',token=NULL WHERE uid=? AND state='checking' AND token=?",
                (job["uid"], job["token"]),
            )

    def finish_follow(self, job: dict, state: str, now: float, error: str = "") -> None:
        if state not in {"mutual", "followed", "requested", "skipped", "rejected", "uncertain"}:
            raise ValueError("无效的回关结果")
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE follow_users SET state=?,updated=?,error=? WHERE uid=? AND token=? "
                "AND state IN ('checking','submitted','uncertain','followed','requested')",
                (state, now, error, job["uid"], job["token"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("回关任务所有权已变化")
            if state in {"uncertain", "rejected"}:
                reason = "outcome_unknown" if state == "uncertain" else "platform_rejected"
                self._pause_follow(db, reason)

    @staticmethod
    def _pause_follow(db, reason: str) -> None:
        db.execute(
            "INSERT INTO meta VALUES ('follow_paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value "
            "WHERE meta.value NOT IN ('operator','platform_rejected')",
            (reason,),
        )

    def recover_follows(self, now: float) -> None:
        with self.transaction() as db:
            db.execute("UPDATE follow_users SET state='queued',token=NULL WHERE state='checking'")
            db.execute("UPDATE follow_users SET state='uncertain',updated=? WHERE state='submitted'", (now,))
            if db.execute("SELECT 1 FROM follow_users WHERE state='uncertain'").fetchone():
                self._pause_follow(db, "outcome_unknown")

    def follow_checks(self) -> list[dict]:
        with contextlib.closing(self.connect()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM follow_users WHERE state IN ('uncertain','followed','requested') "
                    "ORDER BY updated LIMIT 5"
                )
            ]

    def checked_follow(self, job: dict, now: float) -> None:
        with self.transaction() as db:
            db.execute("UPDATE follow_users SET updated=? WHERE uid=? AND token=?", (now, job["uid"], job["token"]))

    def release_follow_pause(self) -> None:
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM follow_users WHERE state='uncertain'").fetchone():
                db.execute("UPDATE meta SET value='' WHERE key='follow_paused' AND value='outcome_unknown'")

    def follow_status(self) -> dict:
        with contextlib.closing(self.connect()) as db:
            counts = dict(db.execute("SELECT state,count(*) FROM follow_users GROUP BY state"))
        return {
            "enabled": self.get_meta("follow_enabled") == "1",
            "baseline_complete": bool(self.get_meta("follow_baseline")),
            "counts": counts,
            "paused": self.get_meta("follow_paused"),
            "last_scan": self.get_meta("follow_last_scan"),
            "scan_pending": bool(self.get_meta("follow_scan")),
            "scan_error": self.get_meta("follow_scan_error"),
        }
