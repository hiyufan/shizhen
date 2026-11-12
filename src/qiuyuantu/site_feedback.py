"""站内意见反馈：页脚和解析结果下方的「意见反馈」表单。

和解析失败反馈（feedback.py）不同，这里是访客自由填写的文字，可能带个人信息，所以不建公开 issue：
存进本机 data/feedback.db 的 messages 表，再发邮件给站长（PARSE_VIDEO_FEEDBACK_NOTIFY，默认同 PARSE_VIDEO_ALERT_TO）。
访客留的邮箱用 feedback.py 的同一把密钥加密暂存，只作为通知邮件的 Reply-To，站长直接回信即可；
通知发出后立刻删除。发信失败的留在库里，后台每 30 分钟重试；最长留 90 天。

表单页附一个签名的时间戳：直接调接口的脚本拿不到，填写太快（少于 3 秒）或过了一天的也不收。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
import re
import time

from . import db, feedback
from .convert import net

log = logging.getLogger("uvicorn.error")

# 收通知的站长邮箱；不单独配置时沿用自检告警的收件人
NOTIFY = (os.environ.get("PARSE_VIDEO_FEEDBACK_NOTIFY") or os.environ.get("PARSE_VIDEO_ALERT_TO") or "").strip()
KINDS = {"page": "页面异常或跳转", "result": "下载或转换不对", "idea": "功能建议", "other": "其他"}
BODY_MAX = 2000
FORM_MIN_SECONDS = 3
FORM_TTL = 24 * 3600
RETRY_INTERVAL = 30 * 60

_SCHEMA = (
    "PRAGMA journal_mode=WAL;"
    "CREATE TABLE IF NOT EXISTS messages ("
    " id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,"
    " page TEXT NOT NULL DEFAULT '', browser TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '',"
    " ip TEXT NOT NULL DEFAULT '', email BLOB, notified REAL);"
)
# 只记大类，够排查兼容问题即可；完整 UA 不入库
_BROWSERS = (
    ("MicroMessenger", "微信"),
    ("Quark", "夸克"),
    ("UCBrowser", "UC"),
    ("MQQBrowser", "QQ 浏览器"),
    ("baiduboxapp", "百度"),
    ("HuaweiBrowser", "华为浏览器"),
    ("HeyTapBrowser", "OPPO 浏览器"),
    ("VivoBrowser", "vivo 浏览器"),
    ("MiuiBrowser", "小米浏览器"),
    ("Edg", "Edge"),
    ("Firefox", "Firefox"),
    ("Chrome", "Chrome"),
    ("CriOS", "Chrome"),
    ("Safari", "Safari"),
)


def enabled() -> bool:
    # 页面答应了「站长会收到」：收件人、加密密钥和发信配置都齐了才显示入口
    return bool(NOTIFY) and feedback.can_encrypt() and feedback.mail_enabled()


def _db():
    return db.transaction(feedback.DB_PATH, _SCHEMA, rows=True)


# --------------------------------------------------------------------------- 表单凭证


def _sig(issued: str) -> str:
    return hmac.new(net.secret_key(), b"site-feedback:" + issued.encode(), hashlib.sha256).hexdigest()[:24]


def form_token() -> str:
    issued = str(int(time.time()))
    return f"{issued}.{_sig(issued)}"


def valid_form_token(token: str) -> bool:
    issued, _, sig = token.partition(".")
    if not issued.isdigit() or not net.secret_equal(_sig(issued), sig):
        return False
    age = time.time() - int(issued)
    return FORM_MIN_SECONDS <= age <= FORM_TTL


# --------------------------------------------------------------------------- 存储


def browser_label(user_agent: str) -> str:
    device = "手机" if re.search(r"Mobile|Android|iPhone|iPad", user_agent) else "电脑"
    name = next((label for key, label in _BROWSERS if key in user_agent), "其他浏览器")
    return f"{name}（{device}）"


def clean_page(path: str) -> str:
    """来源页只留站内路径，不留查询参数（可能是 ?url= 带着分享链接）。"""
    path = re.split(r"[?#]", path.strip(), maxsplit=1)[0]
    return path[:200] if path.startswith("/") and not path.startswith("//") else ""


def add_message(kind: str, body: str, *, page: str, browser: str, link: str, email: str, ip_hash: str) -> int:
    row = (
        time.time(),
        kind,
        body,
        page,
        browser,
        link,
        ip_hash,
        feedback.encrypt_email(email) if email else None,
    )
    with _db() as conn:
        return conn.execute(
            "INSERT INTO messages (created, kind, body, page, browser, link, ip, email) VALUES (?,?,?,?,?,?,?,?)",
            row,
        ).lastrowid


# --------------------------------------------------------------------------- 通知站长


def notice_mail(row) -> tuple[str, str]:
    kind = KINDS.get(row["kind"], row["kind"])
    lines = [
        f"类型：{kind}",
        f"来源页面：{row['page'] or '未知'}",
        f"浏览器：{row['browser'] or '未知'}",
        f"时间：{feedback._when(row['created'])}",
    ]
    if row["link"]:
        lines.append(f"附带链接：{row['link']}")
    lines.append("联系邮箱：已留，直接回复这封邮件即可" if row["email"] else "联系邮箱：未留")
    text = "\n".join(lines) + f"\n\n{row['body']}\n\n— 来自求原图的意见反馈，编号 {row['id']}"
    return f"[求原图] 意见反馈 #{row['id']}：{kind}", text


def _pending() -> list:
    with _db() as conn:
        return conn.execute("SELECT * FROM messages WHERE notified IS NULL ORDER BY id").fetchall()


def _mark_notified(message_id: int) -> None:
    with _db() as conn:
        conn.execute("UPDATE messages SET notified = ?, email = NULL WHERE id = ?", (time.time(), message_id))


def purge_old_emails() -> int:
    with _db() as conn:
        return conn.execute(
            "UPDATE messages SET email = NULL WHERE email IS NOT NULL AND created < ?",
            (time.time() - feedback.EMAIL_TTL_DAYS * 86400,),
        ).rowcount


async def deliver_pending() -> int:
    """把还没通知的反馈逐条发给站长；一封失败就停下，留到下一轮按原顺序重试。返回发出几封。"""
    sent = 0
    for row in await asyncio.to_thread(_pending):
        subject, text = notice_mail(row)
        reply_to = feedback.decrypt_email(row["email"]) if row["email"] else ""
        await asyncio.to_thread(feedback._send_mail, NOTIFY, subject, text, reply_to=reply_to)
        await asyncio.to_thread(_mark_notified, row["id"])
        sent += 1
    await asyncio.to_thread(purge_old_emails)
    return sent


class _Deliverer:
    """新反馈到了马上发；发信一时不通就每 RETRY_INTERVAL 再试。"""

    def __init__(self) -> None:
        self._wake: asyncio.Event | None = None

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def run_forever(self) -> None:
        self._wake = asyncio.Event()
        while True:
            try:
                await deliver_pending()
            except Exception:  # noqa: BLE001 - 邮箱一时不通，下一轮再来
                log.exception("意见反馈通知发送失败")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), RETRY_INTERVAL)
            self._wake.clear()


_deliverer = _Deliverer()
loop = _deliverer.run_forever
# 只能在事件循环线程里调：asyncio.Event 不是线程安全的，入库放在 to_thread 里做完再叫醒
wake = _deliverer.wake
