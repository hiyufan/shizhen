"""解析失败反馈：凭证、邮箱加密、/api/feedback、建 issue、关了先复测再发邮件。GitHub 和邮箱都是假的。"""

import asyncio
import base64
import json
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from parse_video_py import feedback, web
from parse_video_py.convert import limits
from parse_video_py.parser.errors import ParseError

LINK = "https://v.kuaishou.com/n86DkRPN"


@pytest.fixture
def fb(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback, "REPO", "me/fb")
    monkeypatch.setattr(feedback, "GH_TOKEN", "ghp_test")
    monkeypatch.setattr(feedback, "_KEY_B64", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setattr(feedback, "SMTP_USER", "bot@qq.com")
    monkeypatch.setattr(feedback, "SMTP_PASS", "authcode")
    monkeypatch.setattr(feedback, "DB_PATH", tmp_path / "feedback.db")
    monkeypatch.setattr(limits.feedback_limit, "_buckets", {})
    sent = []
    monkeypatch.setattr(feedback, "_send_mail", lambda to, subject, text: sent.append((to, subject, text)))
    return sent


def ticket(reason="empty"):
    return feedback.make_ticket(LINK, reason, "kuaishou", "解析成功但没有拿到任何视频或图片")


# --------------------------------------------------------------------------- 凭证与加密


def test_ticket_only_for_fixable_failures(fb):
    assert feedback.read_ticket(ticket("empty"))["u"] == LINK
    for reason in ("deleted", "restricted", "login", "blocked"):
        assert ticket(reason) is None, reason


def test_ticket_is_off_when_feature_not_configured(fb, monkeypatch):
    monkeypatch.setattr(feedback, "GH_TOKEN", "")
    assert ticket() is None


def test_tampered_or_expired_ticket_is_rejected(fb, monkeypatch):
    t = ticket()
    body, sig = t.split(".")
    forged = feedback._b64(json.dumps({"u": "https://evil.example/", "r": "empty", "p": "x", "m": "", "t": int(time.time())}).encode())
    assert feedback.read_ticket(f"{forged}.{sig}") is None
    assert feedback.read_ticket("garbage") is None
    monkeypatch.setattr(feedback.time, "time", lambda: time.time_ns() / 1e9 + feedback.TICKET_TTL + 60)
    assert feedback.read_ticket(t) is None


def test_email_is_encrypted_at_rest(fb):
    blob = feedback.encrypt_email("someone@example.com")
    assert b"someone" not in blob and feedback.decrypt_email(blob) == "someone@example.com"
    assert feedback.encrypt_email("a@b.cn") != feedback.encrypt_email("a@b.cn")   # 随机 nonce


# --------------------------------------------------------------------------- 接口


@pytest.fixture
def client(fb, monkeypatch):
    web._parse_cache.clear()
    monkeypatch.setattr(limits.parse_limit, "_buckets", {})

    async def fail(url):
        raise ParseError("empty", "解析成功但没有拿到任何视频或图片")

    async def always_safe(url):
        return True

    monkeypatch.setattr(web, "parse_video_share_url", fail)
    monkeypatch.setattr(web, "is_safe_url_async", always_safe)
    return TestClient(web.app)


def test_failed_parse_carries_ticket_and_feedback_is_stored_encrypted(client, fb):
    r = client.get("/api/parse", params={"url": LINK}).json()
    assert r["reason"] == "empty" and r["feedback"]
    out = client.post("/api/feedback", json={"ticket": r["feedback"], "email": "u@example.com"}).json()
    assert out == {"ok": True, "duplicate": False, "email": True}
    again = client.post("/api/feedback", json={"ticket": r["feedback"]}).json()
    assert again["duplicate"] is True and again["email"] is False
    rows = sqlite3.connect(feedback.DB_PATH).execute("SELECT link, email FROM reports").fetchall()
    assert [row[0] for row in rows] == [LINK, LINK]
    assert b"u@example.com" not in rows[0][1] and rows[1][1] is None


def test_feedback_validation_and_rate_limit(client):
    t = client.get("/api/parse", params={"url": LINK}).json()["feedback"]
    assert client.post("/api/feedback", json={"ticket": "x.y"}).status_code == 400
    assert client.post("/api/feedback", json={"ticket": t, "email": "not-an-email"}).status_code == 400
    codes = [client.post("/api/feedback", json={"ticket": t}).status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200] and codes[-1] == 429


def test_feedback_endpoint_hidden_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(feedback, "REPO", "")
    assert client.post("/api/feedback", json={"ticket": "a.b"}).status_code == 404


# --------------------------------------------------------------------------- GitHub 同步与通知


class FakeGitHub:
    def __init__(self):
        self.issues, self.comments, self.reopened, self.next = {}, [], [], 1

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, body = request.url.path, json.loads(request.content or b"{}")
        assert request.headers["authorization"] == "Bearer ghp_test"
        if request.method == "POST" and path.endswith("/issues"):
            n, self.next = self.next, self.next + 1
            self.issues[n] = {"number": n, "state": "open", "title": body["title"], "body": body["body"]}
            return httpx.Response(201, json=self.issues[n])
        n = int(path.split("/issues/")[1].split("/")[0])
        if path.endswith("/comments"):
            self.comments.append((n, body["body"]))
            return httpx.Response(201, json={})
        if request.method == "PATCH":
            self.issues[n].update(body); self.reopened.append(n)
            return httpx.Response(200, json=self.issues[n])
        return httpx.Response(200, json=self.issues[n])


def run(gh, reparse):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gh.handler)) as c:
            await feedback.run_once(reparse, c)
    asyncio.run(go())


async def ok(link):
    return object()


def report(email=""):
    feedback.add_report(feedback.read_ticket(ticket()), email, "iphash")


def test_one_issue_per_link_and_no_email_in_github(fb):
    gh = FakeGitHub()
    report("u@example.com"); report()
    run(gh, ok)
    assert list(gh.issues) == [1] and len(gh.comments) == 1
    issue = gh.issues[1]
    assert "快手" in issue["title"] and LINK in issue["body"]
    assert "example.com" not in issue["body"] + gh.comments[0][1]


def test_closed_issue_is_retested_then_emailed_and_email_deleted(fb):
    gh = FakeGitHub()
    report("u@example.com"); report()
    run(gh, ok)
    gh.issues[1].update(state="closed", state_reason="completed")
    run(gh, ok)
    assert [(to, subj) for to, subj, _ in fb] == [("u@example.com", "你在拾帧反馈的链接已经能解析了")]
    assert LINK in fb[0][2]
    rows = sqlite3.connect(feedback.DB_PATH).execute("SELECT email, outcome FROM reports").fetchall()
    assert rows == [(None, "fixed"), (None, "fixed")]
    run(gh, ok)
    assert len(fb) == 1   # 不会重复发


def test_closed_but_still_failing_is_reopened_without_email(fb):
    gh = FakeGitHub()
    report("u@example.com")
    run(gh, ok)
    gh.issues[1].update(state="closed", state_reason="completed")

    async def still_broken(link):
        raise ParseError("empty", "还是空的")

    run(gh, still_broken)
    assert fb == [] and gh.reopened == [1] and gh.issues[1]["state"] == "open"
    assert sqlite3.connect(feedback.DB_PATH).execute("SELECT email IS NOT NULL FROM reports").fetchone()[0] == 1


def test_wontfix_sends_explanation(fb):
    gh = FakeGitHub()
    report("u@example.com")
    run(gh, ok)
    gh.issues[1].update(state="closed", state_reason="not_planned")
    run(gh, ok)
    assert [subj for _, subj, _ in fb] == ["关于你在拾帧反馈的链接"]


def test_emails_are_purged_after_90_days(fb, monkeypatch):
    report("u@example.com")
    monkeypatch.setattr(feedback.time, "time", lambda: time.time_ns() / 1e9 + 91 * 86400)
    assert feedback.purge_old_emails() == 1
