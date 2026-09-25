"""代理 / 下载时的请求头、SSRF 防护和链接签名。"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
import time
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
    # is_site_local (fec0::/10) 只有 IPv6Address 有, IPv4 上取会抛 AttributeError
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified
            or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local))


def _check_without_dns(url: str) -> tuple[bool | None, str]:
    """不查 DNS 就能下结论的直接给结论; 否则返回 (None, 要查的域名)。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False, ""
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".arpa")):
        return False, host
    try:
        return not _ip_is_internal(ipaddress.ip_address(host)), host
    except ValueError:
        pass  # 普通域名
    if not _DNS_CHECK:
        return True, host
    return _dns_cached(host), host


def is_safe_url(url: str) -> bool:
    """只放行公网 http(s)。

    字面 IP 直接判; 域名解析一次, 解析到内网 / 云元数据地址 (169.254.169.254) 的也拒绝,
    防止用自定义域名或 nip.io 这类服务把代理引到内网。

    同步版本会在调用线程里查 DNS; 事件循环里请用 is_safe_url_async。
    """
    verdict, host = _check_without_dns(url)
    if verdict is not None:
        return verdict
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        infos = []
    return _dns_store(host, infos)


async def is_safe_url_async(url: str) -> bool:
    """同 is_safe_url, 但 DNS 查询放到线程池里。

    getaddrinfo 是同步的, 直接在事件循环上调一次 13-200ms, 这期间所有正在转发的
    视频流、其他人的解析全都停住。站点流量小, 缓存经常是凉的, 这个停顿很常见。
    """
    verdict, host = _check_without_dns(url)
    if verdict is not None:
        return verdict
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        infos = []
    return _dns_store(host, infos)


# 解析结果缓存: 同一个平台域名一次解析里要查好几遍, 缓存一下省掉重复开销。
# 注意这里必须查 AF_UNSPEC: 只查 A 记录会漏掉 AAAA 指向内网的情况, 那是 SSRF 的口子。
_DNS_TTL = float(os.environ.get("PARSE_VIDEO_DNS_TTL", 300))
_dns_cache: dict[str, tuple[float, bool]] = {}


def _dns_cached(host: str) -> bool | None:
    hit = _dns_cache.get(host)
    if hit and time.time() - hit[0] < _DNS_TTL:
        return hit[1]
    return None


def _dns_store(host: str, infos: list) -> bool:
    ok = bool(infos) and not any(_ip_is_internal(ipaddress.ip_address(i[4][0])) for i in infos)
    if len(_dns_cache) > 2048:
        _dns_cache.clear()
    _dns_cache[host] = (time.time(), ok)
    return ok


class UnsafeURL(httpx.RequestError):
    pass


async def _ssrf_request_hook(request: httpx.Request) -> None:
    """挂在 httpx 上, 每一跳 (含 302 之后) 都检查, 外网地址跳到内网也拦得住。"""
    if not await is_safe_url_async(str(request.url)):
        raise UnsafeURL(f"blocked: {request.url.host}", request=request)


_CN_REFERERS = ("bilibili", "xiaohongshu", "douyin", "kuaishou", "weibo", "pipix", "ixigua", "acfun")


def proxy_for_url(url: str) -> str | None:
    """拉 CDN 直链（视频 / 图片本体）时选代理。

    默认不走 PARSE_VIDEO_PROXY_CN：国内平台的 CDN 对海外 IP 一般放行，而视频流量大，
    别把家里宽带 / 小 VPS 的国内出口占满。确实被 CDN 403 时设 PARSE_VIDEO_PROXY_CN_MEDIA=1。
    """
    ref = referer_for(url) or ""
    cn = any(k in ref for k in _CN_REFERERS)
    if cn and os.environ.get("PARSE_VIDEO_PROXY_CN") and os.environ.get("PARSE_VIDEO_PROXY_CN_MEDIA", "0") == "1":
        return os.environ["PARSE_VIDEO_PROXY_CN"]
    return os.environ.get("PARSE_VIDEO_PROXY") or None


class _NoCookies(httpx.Cookies):
    """池化的客户端是跨请求共用的, 不能让 Set-Cookie 攒在一个 jar 里串味。

    原来每次请求都新建客户端, jar 天然是空的; 池化后要显式保持这个行为,
    否则 A 用户解析时平台种下的会话 cookie 会带到 B 用户的请求上。
    """

    def extract_cookies(self, response: httpx.Response) -> None:
        return None

    def set_cookie_header(self, request: httpx.Request) -> None:
        return None


class _PooledClient(httpx.AsyncClient):
    """`async with` 退出时不关闭, 把连接留给下次用。

    调用方一律写成 `async with create_async_client() as c:`, 但每次新建客户端
    就得重做 DNS+TCP+TLS —— 实测对中继要 642ms, 对 B站 CDN 要 435ms。
    真正的关闭由 aclose_pool() 在应用退出时统一做。
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        # httpx 的 __init__ 会把传进来的 cookies 重新包成普通 Cookies, 子类会被丢掉,
        # 只能构造完再换回来, 否则 Set-Cookie 会在池化客户端上跨请求累积
        self._cookies = _NoCookies()

    async def __aenter__(self) -> "_PooledClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def _aclose_for_real(self) -> None:
        await httpx.AsyncClient.aclose(self)


_pool: dict[tuple, _PooledClient] = {}

# 池化只认这几个参数, 出现别的参数就老实新建一个, 免得不同配置的调用互相串
_POOLABLE = {"proxy", "transport", "follow_redirects", "timeout", "http2", "verify"}


def safe_client(for_url: str = "", **kwargs) -> httpx.AsyncClient:
    """带 SSRF 检查的 httpx 客户端, 所有拉取用户提供的地址的地方都用它。

    for_url 给出目标地址时按平台自动选代理；不给则由调用方自己传 proxy。
    同样配置的客户端会复用同一个连接池, 省掉重复握手。
    """
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks.setdefault("request", []).append(_ssrf_request_hook)
    if for_url and "proxy" not in kwargs:
        if proxy := proxy_for_url(for_url):
            kwargs["proxy"] = proxy

    # 带了池化管不了的参数(比如自定义 event_hooks), 就退回一次性客户端
    if hooks.get("request", []) != [_ssrf_request_hook] or set(kwargs) - _POOLABLE:
        return httpx.AsyncClient(event_hooks=hooks, **kwargs)

    key = tuple(sorted((k, _key_part(v)) for k, v in kwargs.items()))
    client = _pool.get(key)
    if client is None or client.is_closed:
        client = _PooledClient(
            event_hooks=hooks,
            limits=httpx.Limits(max_keepalive_connections=40, keepalive_expiry=300.0),
            **kwargs,
        )
        _pool[key] = client
    return client


def _key_part(value: object) -> object:
    """transport / timeout 这类对象不能直接当字典 key。

    Timeout 必须按值取键: 调用方每次都 new 一个 httpx.Timeout(30, read=120),
    按身份取键的话池子只增不命中。transport 那边是单例, 按身份正好。
    """
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, httpx.Timeout):
        return ("timeout", value.connect, value.read, value.write, value.pool)
    return id(value)


async def aclose_pool() -> None:
    for client in list(_pool.values()):
        with contextlib.suppress(Exception):
            await client._aclose_for_real()
    _pool.clear()


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
