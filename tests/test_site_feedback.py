"""站内意见反馈：表单页与入口、/api/feedback/message 校验、邮箱加密暂存、通知站长后删除邮箱。邮箱是假的。"""

import asyncio
import base64
import re
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from lxml import html

from qiuyuantu import feedback, site_feedback, web
from qiuyuantu.web import limits

ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0"
)


@pytest.fixture
def sent(monkeypatch, tmp_path):
    monkeypatch.setattr(site_feedback, "NOTIFY", "owner@example.com")
    monkeypatch.setattr(feedback, "_KEY_B64", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setattr(feedback, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(feedback, "SMTP_PASS", "authcode")
    monkeypatch.setattr(feedback, "DB_PATH", tmp_path / "feedback.db")
    monkeypatch.setattr(limits.message_limit, "_buckets", {})
    mails = []
    monkeypatch.setattr(
        feedback, "_send_mail", lambda to, subject, text, reply_to="": mails.append((to, subject, text, reply_to))
    )
    return mails


def token(age=10):
    issued = str(int(time.time()) - age)
    return f"{issued}.{site_feedback._sig(issued)}"


def message(**overrides):
    data = {"kind": "page", "body": "微信里打开被跳走了", "token": token(), "page": "/douyin?url=https://x"}
    return data | overrides


def rows():
    conn = sqlite3.connect(feedback.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM messages ORDER BY id").fetchall()


# --------------------------------------------------------------------------- 页面与入口


def test_feedback_page_and_entries_follow_configuration(sent, monkeypatch):
    client = TestClient(web.app)
    page = client.get("/feedback")
    assert page.status_code == 200
    doc = html.fromstring(page.text)
    assert doc.xpath('//meta[@name="robots"]/@content') == ["noindex,follow"]
    form = doc.xpath('//form[@id="feedback-form"]')[0]
    assert re.fullmatch(r"\d+\.[0-9a-f]{24}", form.get("data-token"))
    assert doc.xpath('//input[@name="kind"][@checked]/@value') == ["page"]
    # 从结果进来时附链接默认勾选；没有链接时整块由脚本保持隐藏
    assert doc.xpath('//div[@id="fb-attach"][@hidden]//input[@id="fb-attach-box"][@checked]')
    assert html.fromstring(client.get("/feedback?from=result").text).xpath(
        '//input[@name="kind"][@checked]/@value'
    ) == ["result"]
    home = html.fromstring(client.get("/").text)
    assert home.xpath('//footer//a[@href="/feedback"]')
    assert home.xpath('//div[@id="result"]/@data-feedback') == ["/feedback"]

    monkeypatch.setattr(site_feedback, "NOTIFY", "")
    assert client.get("/feedback").status_code == 404
    home = html.fromstring(client.get("/").text)
    assert not home.xpath('//a[@href="/feedback"]') and not home.xpath("//div[@data-feedback]")


def test_english_feedback_page_is_translated(sent):
    doc = html.fromstring(TestClient(web.app).get("/feedback", headers={"Accept-Language": "en"}).text)
    for text in doc.xpath("//section//text() | //title/text() | //meta[@name='description']/@content"):
        assert not re.search(r"[㐀-鿿]", text), text


# --------------------------------------------------------------------------- 提交


def test_message_is_stored_with_encrypted_email_and_minimal_context(sent):
    client = TestClient(web.app)
    body = message(email="u@example.com", link="https://v.douyin.com/abc/", kind="result")
    out = client.post("/api/feedback/message", json=body, headers=ORIGIN | {"User-Agent": WECHAT_UA})
    assert out.json() == {"ok": True, "email": True}
    [row] = rows()
    assert (row["kind"], row["body"], row["page"], row["browser"], row["link"]) == (
        "result",
        "微信里打开被跳走了",
        "/douyin",
        "微信（手机）",
        "https://v.douyin.com/abc/",
    )
    assert b"u@example.com" not in row["email"] and feedback.decrypt_email(row["email"]) == "u@example.com"


@pytest.mark.parametrize(
    ("overrides", "headers", "status"),
    [
        ({}, {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, 403),
        ({"token": lambda: token(age=0)}, ORIGIN, 400),
        ({"token": lambda: token(age=site_feedback.FORM_TTL + 60)}, ORIGIN, 400),
        ({"token": "123.forged"}, ORIGIN, 400),
        ({"kind": "spam"}, ORIGIN, 400),
        ({"body": "   "}, ORIGIN, 400),
        ({"body": "长" * (site_feedback.BODY_MAX + 1)}, ORIGIN, 400),
        ({"email": "not-an-email"}, ORIGIN, 400),
        ({"extra": "x"}, ORIGIN, 422),
    ],
)
def test_message_validation(sent, overrides, headers, status):
    # 凭证在用例运行时才签发：参数在收集阶段就求值，整套测试跑下来「刚打开」的凭证早过了 3 秒
    overrides = {key: value() if callable(value) else value for key, value in overrides.items()}
    response = TestClient(web.app).post("/api/feedback/message", json=message(**overrides), headers=headers)
    assert response.status_code == status
    assert not feedback.DB_PATH.exists() or not rows()


def test_non_http_link_and_offsite_page_are_dropped(sent):
    body = message(link="javascript:alert(1)", page="//evil.example/x")
    assert TestClient(web.app).post("/api/feedback/message", json=body, headers=ORIGIN).status_code == 200
    [row] = rows()
    assert row["link"] == "" and row["page"] == ""


def test_message_rate_limit(sent):
    client = TestClient(web.app)
    codes = [client.post("/api/feedback/message", json=message(), headers=ORIGIN).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


def test_endpoint_hidden_when_not_configured(sent, monkeypatch):
    monkeypatch.setattr(feedback, "SMTP_PASS", "")
    assert TestClient(web.app).post("/api/feedback/message", json=message(), headers=ORIGIN).status_code == 404


# --------------------------------------------------------------------------- 通知站长


def _add(email=""):
    return site_feedback.add_message(
        "idea", "希望支持更多平台", page="/", browser="Chrome（电脑）", link="", email=email, ip_hash="h"
    )


def test_owner_is_notified_with_reply_to_and_email_is_deleted(sent):
    message_id = _add("u@example.com")
    _add()
    assert asyncio.run(site_feedback.deliver_pending()) == 2
    first, second = sent
    assert first[0] == "owner@example.com" and first[3] == "u@example.com"
    assert f"#{message_id}" in first[1] and "功能建议" in first[1]
    assert "希望支持更多平台" in first[2] and "直接回复这封邮件" in first[2] and "u@example.com" not in first[2]
    assert second[3] == "" and "未留" in second[2]
    assert all(row["notified"] and row["email"] is None for row in rows())
    assert asyncio.run(site_feedback.deliver_pending()) == 0


def test_failed_delivery_is_retried_in_order(sent, monkeypatch):
    _add("u@example.com")

    def down(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(feedback, "_send_mail", down)
    with pytest.raises(OSError):
        asyncio.run(site_feedback.deliver_pending())
    assert rows()[0]["notified"] is None and rows()[0]["email"] is not None
    monkeypatch.setattr(feedback, "_send_mail", lambda *args, **kwargs: sent.append(args))
    assert asyncio.run(site_feedback.deliver_pending()) == 1


def test_undelivered_emails_are_purged_after_90_days(sent, monkeypatch):
    _add("u@example.com")
    later = time.time() + (feedback.EMAIL_TTL_DAYS + 1) * 86400
    monkeypatch.setattr(site_feedback.time, "time", lambda: later)
    assert site_feedback.purge_old_emails() == 1
    assert rows()[0]["email"] is None


def test_reply_to_header_is_set_on_outgoing_mail(monkeypatch):
    captured = []

    class FakeSMTP:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            pass

        def send_message(self, msg):
            captured.append(msg)

    monkeypatch.setattr(feedback, "_smtp_connect", FakeSMTP)
    feedback._send_mail("owner@example.com", "主题", "正文", reply_to="u@example.com")
    feedback._send_mail("owner@example.com", "主题", "正文")
    assert captured[0]["Reply-To"] == "u@example.com" and captured[1]["Reply-To"] is None
