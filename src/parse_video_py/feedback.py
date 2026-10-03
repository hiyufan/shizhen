"""解析失败的用户反馈：建 GitHub issue，修好后发邮件告诉留了邮箱的人。

流程：
1. /api/parse 失败、而且是「修得好」的那类（解析出错 / 拿到空的 / 不支持 / 超时），结果里附一张
   签名的反馈凭证（链接 + 原因 + 时间）。没有凭证提交不了反馈，别人没法拿任意链接来刷 issue。
2. /api/feedback 收凭证和选填的邮箱：同一条链接已经有没关的 issue 就追加一条评论，不重复建。
   issue 建在公开仓库里，链接去掉 ? 之后的参数（小红书那种带着访问令牌）；完整链接和邮箱只存在
   本机 data/feedback.db，按 issue 里的「反馈编号」查。邮箱 AES-256-GCM 加密，密钥
   （PARSE_VIDEO_FEEDBACK_KEY）不和数据库放在一起。
3. 后台每 30 分钟看一眼：issue 关了（提交里写 Fixes owner/repo#N）先自己再解析一次那条链接，
   真能用了才发「修好了」的邮件，还是不行就把 issue 重新打开；标成「不修了」的发一封说明。
   邮件发完立刻删邮箱，最长留 90 天。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import smtplib
import sqlite3
import ssl
import time
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

import httpx

from . import db
from .convert import config, net

log = logging.getLogger("uvicorn.error")

REPO = os.environ.get("PARSE_VIDEO_FEEDBACK_REPO", "").strip()  # owner/name
GH_TOKEN = os.environ.get("PARSE_VIDEO_FEEDBACK_GH_TOKEN", "").strip()  # 只给这个仓库 Issues 读写
SMTP_HOST = os.environ.get("PARSE_VIDEO_SMTP_HOST", "smtp.qq.com").strip()
SMTP_PORT = int(os.environ.get("PARSE_VIDEO_SMTP_PORT", "465") or 465)
SMTP_USER = os.environ.get("PARSE_VIDEO_SMTP_USER", "").strip()  # SMTP 登录名（QQ 邮箱就是邮箱地址）
SMTP_PASS = os.environ.get("PARSE_VIDEO_SMTP_PASS", "").strip()  # SMTP 密码 / 授权码
# 发件人地址。QQ 邮箱和登录名是同一个，不用填；Brevo 这类中继登录名是 xxx@smtp-brevo.com，要另填
SMTP_FROM = os.environ.get("PARSE_VIDEO_SMTP_FROM", "").strip() or SMTP_USER
_KEY_B64 = os.environ.get("PARSE_VIDEO_FEEDBACK_KEY", "").strip()  # 32 字节，base64
SITE_URL = os.environ.get("PARSE_VIDEO_SITE_URL", "https://ynvan.com").rstrip("/")

DB_PATH = config.DATA_DIR / "feedback.db"
TICKET_TTL = 24 * 3600
EMAIL_TTL_DAYS = 90
CHECK_INTERVAL = 30 * 60

# 修得好的失败才给反馈：删了 / 平台限制 / 要登录 / 被风控 这些我们改代码也没用
FIXABLE = {"parse", "empty", "unsupported", "timeout"}
PLATFORM_NAMES = {
    "douyin": "抖音",
    "redbook": "小红书",
    "kuaishou": "快手",
    "bilibili": "B站",
    "weibo": "微博",
    "twitter": "X",
    "youtube": "YouTube",
    "ytdlp": "其他站点",
}
REASON_NAMES = {"parse": "解析出错", "empty": "没拿到内容", "unsupported": "不支持的链接", "timeout": "超时"}
_EMAIL_AAD = b"shizhen-feedback-email"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def mail_enabled() -> bool:
    return bool(SMTP_USER and SMTP_PASS)


def enabled() -> bool:
    # 四样都齐才开：页面上答应了「修好发邮件」，发不了信就不该收邮箱
    return bool(REPO and GH_TOKEN and _KEY_B64) and mail_enabled()


# --------------------------------------------------------------------------- 反馈凭证


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sig(body: str) -> str:
    return _b64(hmac.new(net.secret_key(), b"feedback:" + body.encode(), hashlib.sha256).digest()[:18])


def make_ticket(url: str, reason: str, platform: str, msg: str) -> str | None:
    """失败结果里附的反馈凭证；不在 FIXABLE 里的失败、或者功能没开，返回 None（页面就不显示反馈按钮）。"""
    if not enabled() or reason not in FIXABLE or not url:
        return None
    body = _b64(
        json.dumps(
            {"u": url[:2000], "r": reason, "p": platform, "m": msg[:300], "t": int(time.time())},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )
    return f"{body}.{_sig(body)}"


def read_ticket(ticket: str) -> dict | None:
    try:
        body, sig = ticket.split(".", 1)
        if not hmac.compare_digest(sig, _sig(body)):
            return None
        data = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if time.time() - data.get("t", 0) > TICKET_TTL:
        return None
    return data


# --------------------------------------------------------------------------- 邮箱加密


def _aead():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = base64.b64decode(_KEY_B64)
    if len(key) != 32:
        raise ValueError("PARSE_VIDEO_FEEDBACK_KEY 要 32 字节（base64）")
    return AESGCM(key)


def encrypt_email(email: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + _aead().encrypt(nonce, email.encode(), _EMAIL_AAD)


def decrypt_email(blob: bytes) -> str:
    return _aead().decrypt(blob[:12], blob[12:], _EMAIL_AAD).decode()


def valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(_EMAIL.match(email))


# --------------------------------------------------------------------------- 存储

_SCHEMA = (
    "PRAGMA journal_mode=WAL;"
    "CREATE TABLE IF NOT EXISTS reports ("
    " id INTEGER PRIMARY KEY, created REAL NOT NULL, link TEXT NOT NULL, link_key TEXT NOT NULL,"
    " platform TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', msg TEXT NOT NULL DEFAULT '',"
    " ip TEXT NOT NULL DEFAULT '', email BLOB, issue INTEGER, synced INTEGER NOT NULL DEFAULT 0,"
    " notified REAL, outcome TEXT NOT NULL DEFAULT '');"
    "CREATE INDEX IF NOT EXISTS reports_link ON reports (link_key);"
)


def _db():
    return db.transaction(DB_PATH, _SCHEMA, rows=True)


def link_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()[:24]


def add_report(ticket: dict, email: str, ip_hash: str) -> bool:
    """记一条反馈；返回这条链接之前是不是已经有人反馈过（页面上说「已经有人反馈，正在修」）。"""
    key = link_key(ticket["u"])
    row = (
        time.time(),
        ticket["u"],
        key,
        ticket.get("p", ""),
        ticket.get("r", ""),
        ticket.get("m", ""),
        ip_hash,
        encrypt_email(email) if email else None,
    )
    with _db() as conn:
        dup = conn.execute("SELECT 1 FROM reports WHERE link_key = ? AND outcome = '' LIMIT 1", (key,)).fetchone()
        conn.execute(
            "INSERT INTO reports (created, link, link_key, platform, reason, msg, ip, email) VALUES (?,?,?,?,?,?,?,?)",
            row,
        )
    _syncer.wake()
    return bool(dup)


def _unsynced_reports() -> list[sqlite3.Row]:
    with _db() as conn:
        return conn.execute("SELECT * FROM reports WHERE synced = 0 ORDER BY id").fetchall()


def _open_issue_for(key: str) -> int | None:
    """这条链接已经建过、还没处理完的 issue。"""
    with _db() as conn:
        row = conn.execute(
            "SELECT issue FROM reports WHERE link_key = ? AND issue IS NOT NULL AND outcome = '' "
            "ORDER BY id DESC LIMIT 1",
            (key,),
        ).fetchone()
    return row["issue"] if row else None


def _mark_synced(report_id: int, issue: int) -> None:
    with _db() as conn:
        conn.execute("UPDATE reports SET issue = ?, synced = 1 WHERE id = ?", (issue, report_id))


def _pending_by_issue() -> dict[int, list[sqlite3.Row]]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM reports WHERE outcome = '' AND issue IS NOT NULL").fetchall()
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["issue"], []).append(row)
    return grouped


def _close_report(report_id: int, outcome: str) -> None:
    """处理完了：记下结论，邮箱立刻删掉。"""
    with _db() as conn:
        conn.execute(
            "UPDATE reports SET email = NULL, notified = ?, outcome = ? WHERE id = ?",
            (time.time(), outcome, report_id),
        )


def purge_old_emails() -> int:
    if not DB_PATH.exists():
        return 0
    with _db() as conn:
        return conn.execute(
            "UPDATE reports SET email = NULL WHERE email IS NOT NULL AND created < ?",
            (time.time() - EMAIL_TTL_DAYS * 86400,),
        ).rowcount


# --------------------------------------------------------------------------- GitHub


class GitHub:
    def __init__(self, client: httpx.AsyncClient):
        self.c = client

    async def call(self, method: str, path: str, **kw) -> dict:
        r = await self.c.request(
            method,
            f"https://api.github.com/repos/{REPO}{path}",
            headers={
                "Authorization": f"Bearer {GH_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "shizhen-feedback",
            },
            timeout=20,
            **kw,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    async def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        try:
            return (await self.call("POST", "/issues", json={"title": title, "body": body, "labels": labels}))["number"]
        except httpx.HTTPStatusError as err:
            if err.response.status_code != 422:
                raise
            # 标签建不了（令牌没权限 / 名字不合规）就不带标签建，issue 本身最要紧
            return (await self.call("POST", "/issues", json={"title": title, "body": body}))["number"]

    async def comment(self, number: int, body: str) -> None:
        await self.call("POST", f"/issues/{number}/comments", json={"body": body})

    async def issue(self, number: int) -> dict:
        return await self.call("GET", f"/issues/{number}")

    async def reopen(self, number: int, body: str) -> None:
        await self.comment(number, body)
        await self.call("PATCH", f"/issues/{number}", json={"state": "open"})


def _when(ts: float) -> str:
    # 容器里没设时区（UTC），issue 和评论里统一写北京时间
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + 8 * 3600)) + "（北京时间）"


def public_link(url: str) -> str:
    """issue 在公开仓库里：链接去掉 ? 和 # 后面的部分（小红书这类分享链接里带着访问令牌）。
    完整链接只在本机 feedback.db 里，按反馈编号取。"""
    return re.split(r"[?#]", url, maxsplit=1)[0]


def _issue_text(row: sqlite3.Row) -> tuple[str, str, list[str]]:
    plat = PLATFORM_NAMES.get(row["platform"], row["platform"] or "未知平台")
    reason = REASON_NAMES.get(row["reason"], row["reason"])
    link = public_link(row["link"])
    trimmed = link != row["link"]
    body = (
        f"**平台**：{plat}\n**原因**：{reason}（`{row['reason']}`）\n**报错**：{row['msg'] or '无'}\n"
        f"**链接**：{link}" + ("（参数已去掉，完整链接在服务器上）" if trimmed else "") + "\n"
        f"**反馈编号**：{row['id']}（服务器 `data/feedback.db` 里按编号查完整链接）\n"
        f"**反馈时间**：{_when(row['created'])}\n\n"
        + ("" if trimmed else f"在线复现：{SITE_URL}/?url={quote(link, safe='')}&src=issue\n\n")
        + "这条 issue 由 ynvan.com 用户在解析失败后反馈自动创建。修好后提交信息里写 `Fixes #<编号>`；"
        "issue 关闭后服务器会先自己再解析一次，确认能用了才给留了邮箱的人发邮件（邮箱不在这里，只加密存在服务器上）。"
    )
    return f"[{plat}] {reason}：{re.sub(r'^https?://', '', link)[:60]}", body, ["用户反馈", plat, reason]


async def _sync_issues(gh: GitHub) -> None:
    """没同步到 GitHub 的反馈：同一链接已有 issue 就评论 +1，没有就建一个。"""
    for row in _unsynced_reports():
        number = _open_issue_for(row["link_key"])
        if number is None:
            number = await gh.create_issue(*_issue_text(row))
        else:
            left_email = "，留了邮箱。" if row["email"] else "。"
            await gh.comment(number, f"又有一人反馈（{_when(row['created'])}）{left_email}")
        _mark_synced(row["id"], number)


# --------------------------------------------------------------------------- 邮件


def _send_mail(to: str, subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr(("求原图", SMTP_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    with _smtp_connect() as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


def _smtp_connect() -> smtplib.SMTP:
    """465 一上来就是 TLS（QQ 邮箱）；587 等其它端口先明文连上再 STARTTLS（Brevo），升级不了就别发。"""
    if SMTP_PORT == 465:
        return smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    try:
        s.starttls(context=ssl.create_default_context())
    except BaseException:
        s.close()
        raise
    return s


def fixed_mail(link: str) -> tuple[str, str]:
    return (
        "你在求原图反馈的链接已经能解析了",
        (
            "你好，\n\n"
            f"你之前在求原图（{SITE_URL}）反馈过一条解析失败的链接：\n{link}\n\n"
            "这个问题已经修好了，我们刚刚又解析了一次，确认可以用。点这里直接打开：\n"
            f"{SITE_URL}/?url={quote(link, safe='')}\n\n"
            "谢谢你的反馈。这是一封一次性通知，你的邮箱在邮件发出后已经删除，之后不会再收到我们的邮件。\n\n— 求原图"
        ),
    )


def wontfix_mail(link: str) -> tuple[str, str]:
    return (
        "关于你在求原图反馈的链接",
        (
            "你好，\n\n"
            f"你之前在求原图（{SITE_URL}）反馈过一条解析失败的链接：\n{link}\n\n"
            "我们看过了，这个问题暂时没法解决，多半是平台那边限制了这类内容。抱歉没能帮上忙。\n\n"
            "这是一封一次性通知，你的邮箱在邮件发出后已经删除。\n\n— 求原图"
        ),
    )


_MAILS = {"fixed": fixed_mail, "wontfix": wontfix_mail}
_OUTCOME_NAMES = {"fixed": "已修好", "wontfix": "暂不修"}

Reparse = Callable[[str], Awaitable[object]]


async def _outcome(gh: GitHub, number: int, issue: dict, link: str, reparse: Reparse) -> str | None:
    """关掉的 issue 算哪种结论。标了「已完成」的先复测，还是失败就重新打开，返回 None。"""
    if issue.get("state_reason") == "not_planned":
        return "wontfix"
    try:
        await reparse(link)
    except Exception as err:  # noqa: BLE001 - 复测失败就别发「修好了」
        await gh.reopen(number, f"issue 关了，但服务器复测还是失败：{str(err)[:200]}\n先重新打开。")
        return None
    return "fixed"


async def _mail_reporters(reports: list[sqlite3.Row], outcome: str) -> int:
    """给留了邮箱的人各发一封，然后把这批反馈都结掉（邮箱随之删除）。返回发了几封。"""
    subject, text = _MAILS[outcome](reports[0]["link"])
    sent = 0
    for row in reports:
        if row["email"] and mail_enabled():
            await asyncio.to_thread(_send_mail, decrypt_email(row["email"]), subject, text)
            sent += 1
        _close_report(row["id"], outcome)
    return sent


async def _notify(gh: GitHub, reparse: Reparse) -> None:
    """issue 关了的：completed 先复测，能用才发「修好了」，不行就重新打开；not_planned 发说明。"""
    for number, reports in _pending_by_issue().items():
        issue = await gh.issue(number)
        if issue.get("state") != "closed":
            continue
        outcome = await _outcome(gh, number, issue, reports[0]["link"], reparse)
        if outcome is None:
            continue
        if sent := await _mail_reporters(reports, outcome):
            await gh.comment(number, f"已给 {sent} 位留了邮箱的反馈者发邮件（{_OUTCOME_NAMES[outcome]}），邮箱已删除。")


# --------------------------------------------------------------------------- 后台循环


async def run_once(reparse: Reparse, client: httpx.AsyncClient | None = None) -> None:
    async with contextlib.AsyncExitStack() as stack:
        if client is None:
            client = await stack.enter_async_context(httpx.AsyncClient())
        gh = GitHub(client)
        await _sync_issues(gh)
        await _notify(gh, reparse)
        purge_old_emails()


class _Syncer:
    """后台同步：有新反馈马上同步一次，平时每 CHECK_INTERVAL 看一眼 issue 关没关。"""

    def __init__(self) -> None:
        self._wake: asyncio.Event | None = None

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def run_forever(self, reparse: Reparse) -> None:
        self._wake = asyncio.Event()
        while True:
            try:
                await run_once(reparse)
            except Exception:  # noqa: BLE001 - GitHub / 邮箱一时不通，下一轮再来
                log.exception("反馈同步失败")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), CHECK_INTERVAL)
            self._wake.clear()


_syncer = _Syncer()
loop = _syncer.run_forever
