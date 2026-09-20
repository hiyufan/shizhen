"""代理 / 下载时的请求头、SSRF 防护和链接签名。"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
from urllib.parse import urlparse

import httpx

from . import config

# 各家 CDN 直链需要带的 Referer，缺了会 403
_REFERERS = {
    "bilivideo.com": "https://www.bilibili.com/",
    "hdslb.com": "https://www.bilibili.com/",
    "acfun.cn": "https://www.acfun.cn/",
    "xhscdn.com": "https://www.xiaohongshu.com/",
    "xiaohongshu.com": "https://www.xiaohongshu.com/",
    "douyinvod.com": "https://www.douyin.com/",
    "douyinpic.com": "https://www.douyin.com/",
    "365yg.com": "https://www.douyin.com/",
    "snssdk.com": "https://www.douyin.com/",
    "douyinstatic.com": "https://www.douyin.com/",
    "kuaishou.com": "https://www.kuaishou.com/",
    "kwaicdn.com": "https://www.kuaishou.com/",
    "yximgs.com": "https://www.kuaishou.com/",
    "twimg.com": "https://x.com/",
    "sinaimg.cn": "https://weibo.com/",
    "weibocdn.com": "https://weibo.com/",
    "miaopai.com": "https://weibo.com/",
    "pipix.com": "https://h5.pipix.com/",
    "ixigua.com": "https://www.ixigua.com/",
    "tiktokcdn.com": "https://www.tiktok.com/",
    "cdninstagram.com": "https://www.instagram.com/",
    "pinimg.com": "https://www.pinterest.com/",
}


def referer_for(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    for suffix, ref in _REFERERS.items():
        if host == suffix or host.endswith("." + suffix):
            return ref
    return None


def headers_for(url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": config.DESKTOP_UA, "Accept": "*/*"}
    if ref := referer_for(url):
        headers["Referer"] = ref
    if extra:
        headers.update({k: v for k, v in extra.items() if k.lower() not in ("host", "content-length", "cookie")})
    return headers


# ------------------------------------------------------------------ SSRF 防护

# Clash / Surge 一类代理的 fake-ip 段: 域名一律解析到这里, 不代表真的是内网
_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
_DNS_CHECK = os.environ.get("PARSE_VIDEO_SSRF_DNS", "1") == "1"


def _ip_is_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip in _FAKE_IP:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or ip.is_site_local)


def is_safe_url(url: str) -> bool:
    """只放行公网 http(s)。

    字面 IP 直接判; 域名解析一次, 解析到内网 / 云元数据地址 (169.254.169.254) 的也拒绝,
    防止用自定义域名或 nip.io 这类服务把代理引到内网。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".arpa")):
        return False
    try:
        return not _ip_is_internal(ipaddress.ip_address(host))
    except ValueError:
        pass  # 普通域名
    if not _DNS_CHECK:
        return True
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    return bool(infos) and not any(_ip_is_internal(ipaddress.ip_address(i[4][0])) for i in infos)


class UnsafeURL(httpx.RequestError):
    pass


async def _ssrf_request_hook(request: httpx.Request) -> None:
    """挂在 httpx 上, 每一跳 (含 302 之后) 都检查, 外网地址跳到内网也拦得住。"""
    if not is_safe_url(str(request.url)):
        raise UnsafeURL(f"blocked: {request.url.host}", request=request)


def safe_client(**kwargs) -> httpx.AsyncClient:
    """带 SSRF 检查的 httpx 客户端, 所有拉取用户提供的地址的地方都用它。"""
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks.setdefault("request", []).append(_ssrf_request_hook)
    proxy = os.environ.get("PARSE_VIDEO_PROXY")
    if proxy and "proxy" not in kwargs:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)


# ------------------------------------------------------------------ 链接签名: 代理只转发我们自己解析出来的地址

_SECRET: bytes | None = None


def _secret() -> bytes:
    global _SECRET
    if _SECRET:
        return _SECRET
    env = os.environ.get("PARSE_VIDEO_SECRET")
    if env:
        _SECRET = env.encode()
        return _SECRET
    config.ensure_dirs()
    key_file = config.DATA_DIR / "secret.key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    _SECRET = key_file.read_text(encoding="utf-8").strip().encode()
    return _SECRET


def sign(url: str) -> str:
    return hmac.new(_secret(), url.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def verify(url: str, sig: str | None) -> bool:
    return bool(sig) and hmac.compare_digest(sign(url), sig)


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_filename(name: str, ext: str, fallback: str = "media") -> str:
    base = _UNSAFE.sub(" ", name or "").strip().strip(".")
    base = re.sub(r"\s+", " ", base)[:60].strip() or fallback
    ext = ext.lstrip(".")
    return f"{base}.{ext}" if ext else base


def scrub(text: str) -> str:
    """错误信息里别带服务器本地路径。"""
    return text.replace(str(config.DATA_DIR), "data").replace(str(config.PROJECT_DIR), ".")
