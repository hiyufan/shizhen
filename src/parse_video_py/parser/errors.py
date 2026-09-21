"""结构化的解析错误：前端能据此说人话，而不是统一一句"解析失败"。"""
from __future__ import annotations

import re

# reason -> 给用户看的一句话
REASONS = {
    "deleted": "这条内容已经被删除、设为私密，或者链接过期了",
    "login": "这个平台现在要求登录才能看，需要站长给服务器配置 cookies",
    "blocked": "平台暂时限制了服务器的访问，过几分钟再试，或者换个链接",
    "unsupported": "还不支持这个链接的格式",
    "network": "连不上对方平台，稍后再试",
    "timeout": "对方平台响应太慢，稍后再试",
    "empty": "解析成功但没有拿到任何视频或图片",
    "parse": "平台页面结构变了，解析器需要更新",
}


class ParseError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason if reason in REASONS else "parse"
        self.detail = detail
        super().__init__(REASONS[self.reason] + (f"（{detail}）" if detail else ""))


_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("deleted", re.compile(r"status_deleted|作品不见了|已被删除|不存在|undefined|private|removed|unavailable|has been deleted|404|没有作品|tombstone|parse video ID|video ID from", re.I)),
    ("login", re.compile(r"sign in|log ?in|login|cookies|authentication|登录|账号|需要.*登录", re.I)),
    ("blocked", re.compile(r"429|too many|rate ?limit|forbidden|403|412|风控|验证|captcha|bot|blocked|access denied|encrypt_data_miss|限流|拒绝了服务器", re.I)),
    ("timeout", re.compile(r"timed? ?out|timeout", re.I)),
    ("network", re.compile(r"connect|connection|network|dns|ssl|reset by peer|remote ?protocol", re.I)),
    ("unsupported", re.compile(r"unsupported|not support|does not have source|无法从 URL|not implemented|暂不支持", re.I)),
]


def classify(exc: BaseException) -> ParseError:
    """把上游 / yt-dlp / httpx 抛出来的各种异常归到几个原因里。"""
    if isinstance(exc, ParseError):
        return exc
    name = exc.__class__.__name__
    text = f"{name}: {exc}"
    if name in ("TimeoutError", "ReadTimeout", "ConnectTimeout", "PoolTimeout"):
        return ParseError("timeout")
    if "中继" in text:
        return ParseError("network", str(exc)[:80])
    if name in ("ConnectError", "RemoteProtocolError", "ProxyError", "NetworkError"):
        return ParseError("network")
    for reason, pattern in _RULES:
        if pattern.search(text):
            return ParseError(reason)
    return ParseError("parse", str(exc)[:120])
