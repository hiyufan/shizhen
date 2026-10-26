"""视频及实况的私信选择：持久化提示、绑定发送者、一次选择与到期回收。"""

from __future__ import annotations

import contextlib
import json
import re
import unicodedata
import uuid

CHOICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_choices (
 job TEXT PRIMARY KEY REFERENCES jobs(id), code TEXT UNIQUE NOT NULL,
 artifact TEXT NOT NULL, manifest TEXT NOT NULL, expires REAL, mode TEXT, prompt_started REAL
);
CREATE TABLE IF NOT EXISTS choice_events (
 id TEXT PRIMARY KEY, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS choice_followups (
 conversation TEXT NOT NULL, sender TEXT NOT NULL, mode TEXT NOT NULL,
 candidates TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
 PRIMARY KEY(conversation, sender)
);
"""

MODES = {
    "1": "video",
    "原视频": "video",
    "视频": "video",
    "video": "video",
    "live": "video",
    "实况": "video",
    "原实况": "video",
    "2": "gif",
    "gif": "gif",
    "动图": "gif",
    "3": "both",
    "都要": "both",
    "两者都要": "both",
    "both": "both",
}


def parse_selection(text: str) -> dict | None:
    text = unicodedata.normalize("NFKC", text).replace("\ufe0f", "").replace("\u20e3", "").strip().lower()
    match = re.fullmatch(r"([^\s#]+)(?:\s*#?\s*([a-f0-9]{4,8}))?", text)
    if not match or match[1] not in MODES:
        code = re.fullmatch(r"#?([a-f0-9]{4,8})", text)
        return {"mode": None, "code": code[1].upper()} if code else None
    return {"mode": MODES[match[1]], "code": (match[2] or "").upper()}


def choice_prompt(code: str, manifest: dict, settings) -> str:
    live = bool(manifest.get("live_photo_count") or manifest.get("native_live"))
    original = "LIVE 实况（保存为实况）" if live else "原视频"
    alias = "LIVE" if live else "原视频"
    title = " ".join(str(manifest.get("title") or ("实况照片" if live else "视频")).split())[:50]
    count = manifest.get("live_photo_count", 0)
    scope = f"本次选择适用于全部 {count} 张实况，普通图片随 LIVE 返回。\n" if count > 1 else ""
    return (
        f"「{title}」已识别，想要哪种？\n"
        f"1 {original}\n2 GIF（每段前 {settings.gif_seconds:g} 秒）\n3 都要\n"
        f"{scope}回复 1 / 2 / 3，或 {alias} / GIF / 都要。\n"
        f"编号 {code}；多条同时等待请回复“2 {code[:4]}”（编号可写前 4 位，重号需补全）。"
        f"{settings.choice_seconds / 60:g} 分钟内有效。\n"
        f"查看进度回复“进度 {code}”，取消回复“取消 {code}”。"
    )


class VideoChoices:
    def set_choice_source(self, job: dict, artifact: str, manifest: dict) -> None:
        with self.transaction() as db:
            self._owned(db, job)
            db.execute(
                "UPDATE video_choices SET artifact=?,manifest=? WHERE job=?",
                (artifact, json.dumps(manifest), job["id"]),
            )

    def _offer_video(self, db, job: dict, artifact: str, manifest: dict) -> list[dict]:
        code = uuid.uuid4().hex[:8].upper()
        while db.execute("SELECT 1 FROM video_choices WHERE code=?", (code,)).fetchone():
            code = uuid.uuid4().hex[:8].upper()
        db.execute(
            "INSERT INTO video_choices(job,code,artifact,manifest) VALUES (?,?,?,?)",
            (job["id"], code, artifact, json.dumps(manifest)),
        )
        return [{"text": choice_prompt(code, manifest, self.settings)}]

    def warm_candidates(self, now: float, limit: int = 20) -> list[dict]:
        """已出提示、尚未选择也未过期的视频 / 实况，供空闲时预先准备原格式。"""
        with contextlib.closing(self.connect()) as db:
            rows = db.execute(
                "SELECT c.job,c.manifest,j.conversation,j.payload FROM video_choices c JOIN jobs j ON j.id=c.job "
                "WHERE c.mode IS NULL AND coalesce(c.expires,?)>? AND j.state IN ('ready','sending','waiting') "
                "ORDER BY j.received LIMIT ?",
                (now + 1, now, limit),
            )
            return [dict(row) for row in rows]

    def warm_choice_source(self, job_id: str, previous: str, artifact: str, manifest: dict) -> bool:
        # 预热不持有任务租约，只在来源仍是读取时那份时替换；正式准备已改写来源就保留正式结果。
        # 写入 video_choices 后缓存受待选任务保护，选择后直接用本地文件，不再按元信息重新下载。
        with self.transaction() as db:
            return (
                db.execute(
                    "UPDATE video_choices SET artifact=?,manifest=? WHERE job=? AND manifest=?",
                    (artifact, json.dumps(manifest), job_id, previous),
                ).rowcount
                == 1
            )

    def selection(self, job: dict) -> dict | None:
        with contextlib.closing(self.connect()) as db:
            row = db.execute("SELECT * FROM video_choices WHERE job=?", (job["id"],)).fetchone()
            return dict(row) if row and row["mode"] in {"video", "gif", "both"} else None

    def after_delivery(self, db, job_id: str, error: str | None, now: float) -> str:
        choice = db.execute("SELECT mode FROM video_choices WHERE job=?", (job_id,)).fetchone()
        if not error and choice and choice["mode"] is None:
            db.execute(
                "UPDATE video_choices SET expires=coalesce(expires,?) WHERE job=?",
                (now + self.settings.choice_seconds, job_id),
            )
            return "waiting"
        return self._completed_state(error)

    def _choice_candidates(self, db, message, now: float) -> list:
        return db.execute(
            "SELECT c.*,j.created FROM video_choices c JOIN jobs j ON j.id=c.job "
            "WHERE j.conversation=? AND j.sender=? AND j.state='waiting' AND c.mode IS NULL "
            "AND c.expires>? AND c.prompt_started<=? ORDER BY j.received",
            (message.conversation, message.sender, now, message.created),
        ).fetchall()

    @staticmethod
    def _prompt_pending(db, message) -> bool:
        # 客户端可能先显示提示、后返回发送回执；实时收件不能提前吞掉用户的回复。
        return bool(
            db.execute(
                "SELECT 1 FROM video_choices c JOIN jobs j ON j.id=c.job "
                "WHERE j.conversation=? AND j.sender=? AND c.mode IS NULL AND c.expires IS NULL "
                "AND j.state IN ('ready','sending','uncertain') AND c.prompt_started<=? "
                "AND c.code LIKE ? LIMIT 1",
                (message.conversation, message.sender, message.created, message.data["code"] + "%"),
            ).fetchone()
        )

    def select_video(self, message, now: float) -> str:
        if message.data.get("mode") not in {None, "video", "gif", "both"}:
            return "invalid"
        floor = self.message_floor(message.conversation, now)
        if message.created < floor or not floor <= message.created <= now + 60:
            return "old"
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM choice_events WHERE id=?", (message.key,)).fetchone():
                return "duplicate"
            allowed = self.command_allowed(db, message, now)
            if allowed and self._prompt_pending(db, message):
                return "deferred"
            db.execute("INSERT INTO choice_events VALUES (?,?)", (message.key, now))
            if not allowed:
                return "limited"
            rows = self._choice_candidates(db, message, now)
            mode, matches = self._selection_matches(db, message, rows, now)
            if mode and len(matches) == 1:
                row = matches[0]
                db.execute("UPDATE video_choices SET mode=? WHERE job=?", (mode, row["job"]))
                db.execute(
                    "UPDATE jobs SET state='queued',attempts=0,available=?,updated=? WHERE id=?", (now, now, row["job"])
                )
                self._measure(
                    db, row["job"], "choice_wait", max(0, now - row["expires"] + self.settings.choice_seconds), now
                )
                db.execute(
                    "DELETE FROM choice_followups WHERE conversation=? AND sender=? AND created<=?",
                    (message.conversation, message.sender, message.created),
                )
                return "selected"
            if mode and len(matches) > 1:
                self._remember_choice(db, message, mode, matches)
            if rows and mode:
                self._selection_notice(db, message, matches or rows, mode if matches else None, now)
            return "ambiguous" if len(matches) > 1 else "unmatched"

    @staticmethod
    def _selection_matches(db, message, rows: list, now: float) -> tuple[str | None, list]:
        mode, code = message.data.get("mode"), message.data["code"]
        if mode is None:
            followup = db.execute(
                "SELECT * FROM choice_followups WHERE conversation=? AND sender=? AND created<=? AND expires>?",
                (message.conversation, message.sender, message.created, max(now, message.created)),
            ).fetchone()
            if not followup or not code:
                return None, []
            mode = followup["mode"]
            candidates = json.loads(followup["candidates"])
            rows = [row for row in rows if row["job"] in candidates]
        return mode, [row for row in rows if row["code"].startswith(code)]

    @staticmethod
    def _remember_choice(db, message, mode: str, rows: list) -> None:
        # 只记住本次歧义中的候选，后来的作品不能继承旧选择；补编号仍核对发送者、会话和期限。
        db.execute(
            "INSERT INTO choice_followups VALUES (?,?,?,?,?,?) ON CONFLICT(conversation,sender) DO UPDATE SET "
            "mode=excluded.mode,candidates=excluded.candidates,created=excluded.created,expires=excluded.expires "
            "WHERE excluded.created>=choice_followups.created",
            (
                message.conversation,
                message.sender,
                mode,
                json.dumps([row["job"] for row in rows]),
                message.created,
                min(row["expires"] for row in rows),
            ),
        )

    def _selection_notice(self, db, message, rows: list, mode: str | None, now: float) -> None:
        # 回复错误也不能无限扩张队列；同一会话五分钟只提示一次。
        recent = db.execute(
            "SELECT 1 FROM jobs WHERE conversation=? AND error='choice_hint' AND received>?",
            (message.conversation, now - 300),
        ).fetchone()
        pending = db.execute(
            "SELECT count(*) FROM jobs WHERE state IN "
            "('queued','processing','ready','sending','uncertain','waiting','blocked')"
        ).fetchone()[0]
        if recent or pending >= self.settings.max_pending:
            return
        text = self._selection_hint(rows, mode)
        db.execute(
            "INSERT INTO jobs(id,conversation,sender,message_id,created,received,"
            "payload,state,available,error,updated) "
            "VALUES (?,?,?,?,?,?,?,'ready',?,'choice_hint',?)",
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
    def _selection_hint(rows: list, mode: str | None) -> str:
        label = {"video": "原格式", "gif": "GIF", "both": "都要"}.get(mode)
        heading = f"已选{label}，请再选一条素材，单独回复编号即可：" if label else "请回复格式和素材编号："
        lines = []
        for row in rows[:10]:
            short = row["code"][:4]
            code = short if sum(other["code"].startswith(short) for other in rows) == 1 else row["code"]
            title = " ".join(str(json.loads(row["manifest"]).get("title") or "素材").split())[:30]
            lines.append(f"{code} · {title}")
        return "\n".join([heading, *lines, "也可回复“2 编号”选 GIF，或“1 编号”选原格式、“3 编号”选都要。"])

    def expire_choices(self, now: float) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM choice_followups WHERE expires<=?", (now,))
            rows = db.execute(
                "SELECT c.job FROM video_choices c JOIN jobs j ON j.id=c.job "
                "WHERE j.state='waiting' AND c.mode IS NULL AND c.expires<=?",
                (now,),
            ).fetchall()
            for row in rows:
                db.execute("UPDATE video_choices SET mode='expired' WHERE job=?", (row["job"],))
                number = self._next_part(db, row["job"])
                db.execute(
                    "INSERT INTO parts(job,number,payload) VALUES (?,?,?)",
                    (
                        row["job"],
                        number,
                        json.dumps(
                            {"text": "这条素材的选择已过期，已取消发送。需要时请重新转发。"}, ensure_ascii=False
                        ),
                    ),
                )
                db.execute(
                    "UPDATE jobs SET state='ready',error='choice_expired',available=?,updated=? WHERE id=?",
                    (now, now, row["job"]),
                )
