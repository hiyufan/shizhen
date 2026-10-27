"""访问来源：短期签名会话，只保留来源域名、推广标记和站内路径。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from .convert import net

COOKIE = "qyt_visit"
MAX_AGE = 1800
_TAG = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_SEARCH = {
    "google": (
        "google.com",
        "google.com.hk",
        "google.com.tw",
        "google.cn",
        "google.co.jp",
        "google.co.uk",
        "google.de",
        "google.fr",
        "google.ca",
        "google.com.au",
        "google.co.in",
        "google.co.kr",
        "google.com.sg",
    ),
    "baidu": ("baidu.com",),
    "bing": ("bing.com",),
    "duckduckgo": ("duckduckgo.com",),
    "sogou": ("sogou.com",),
    "360": ("so.com",),
    "yandex": ("yandex.com", "yandex.ru"),
    "yahoo": ("search.yahoo.com", "search.yahoo.co.jp"),
}
_SOCIAL = {
    "telegram": ("t.me", "telegram.org"),
    "x": ("t.co", "twitter.com", "x.com"),
    "wechat": ("weixin.qq.com", "servicewechat.com"),
    "reddit": ("reddit.com",),
}


@dataclass(frozen=True)
class Visit:
    id: str
    started: float
    channel: str
    source: str
    medium: str
    campaign: str
    landing: str


def tag(value: str) -> str:
    """推广标记使用短 ASCII 名称；不保存 URL、邮箱或自由文本。"""
    value = value.lower().strip()
    return value if _TAG.fullmatch(value) else ""


def hostname(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        if parts.scheme not in {"https", "http"} or parts.username or parts.password:
            return ""
        return host.encode("idna").decode("ascii") if len(host) <= 253 else ""
    except (ValueError, UnicodeError):
        return ""


def _provider(host: str, providers: dict) -> str:
    return next(
        (name for name, domains in providers.items() if any(host == d or host.endswith("." + d) for d in domains)),
        "",
    )


def origin(referrer: str, site_host: str, params) -> tuple[str, str, str, str] | None:
    source = tag(params.get("utm_source", ""))
    if source:
        medium, campaign = tag(params.get("utm_medium", "")), tag(params.get("utm_campaign", ""))
        channel = "paid" if medium in {"cpc", "ppc", "paid", "paid_social", "display"} else "campaign"
        if medium == "organic" and source in _SEARCH:
            channel = "search"
        return channel, source, medium, campaign
    host = hostname(referrer)
    if not host or host.removeprefix("www.") == site_host.removeprefix("www."):
        return None
    if engine := _provider(host, _SEARCH):
        return "search", engine, "organic", ""
    if social := _provider(host, _SOCIAL):
        return "social", social, "referral", ""
    return "referral", host, "referral", ""


def choose(previous: Visit | None, landing: str, evidence: tuple | None) -> Visit:
    # 同站跳转和刷新保留来源；新的推广来源开始新会话，避免被前一个渠道抢走。
    if previous and (
        evidence is None or evidence == (previous.channel, previous.source, previous.medium, previous.campaign)
    ):
        return previous
    channel, source, medium, campaign = evidence or ("direct", "", "", "")
    return Visit(secrets.token_hex(16), time.time(), channel, source, medium, campaign, landing)


def _signature(payload: str) -> str:
    return hmac.new(net.secret_key(), b"visit-v1:" + payload.encode(), hashlib.sha256).hexdigest()


def encode(visit: Visit) -> str:
    raw = json.dumps({"visit": asdict(visit), "expires": int(time.time()) + MAX_AGE}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return payload + "." + _signature(payload)


def decode(cookie: str) -> Visit | None:
    if not cookie or len(cookie) > 2048:
        return None
    payload, _, signature = cookie.partition(".")
    if not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(signature, _signature(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not time.time() < data["expires"] <= time.time() + MAX_AGE + 1:
            return None
        return Visit(**data["visit"])
    except (ValueError, TypeError, KeyError):
        return None
