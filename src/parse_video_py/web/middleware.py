"""全站中间件：安全响应头、缓存头，以及页面浏览的统计。"""

from __future__ import annotations

import base64
import hashlib
import re

from fastapi import Request, Response
from fastapi.responses import RedirectResponse

from .. import seo, stats
from ..convert import relay
from . import limits

_INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_SCRIPT_ORIGIN = re.compile(r"<script\b[^>]*\bsrc=[\"']?(https://[^/\"'\s>]+)", re.I)


def _inline_hash(body: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


def build_csp(analytics_html: str = "", edge_origin: str = "", media_origin: str = "") -> str:
    """页面上我们自己的脚本都是 /js/ 下的外部文件，不许跑任何内联脚本（XSS 注入进来也执行不了）。
    站长配的统计代码（PARSE_VIDEO_ANALYTICS）是唯一的例外：里面的内联脚本按内容哈希放行，
    它引用的外部统计域名可以加载脚本、发图片打点和请求。JSON-LD 是数据块，浏览器不执行，不受影响。"""
    hashes = [_inline_hash(body) for body in _INLINE_SCRIPT.findall(analytics_html) if body.strip()]
    origins = sorted(set(_SCRIPT_ORIGIN.findall(analytics_html)))
    third_party = "".join(" " + item for item in hashes + origins)
    hosts = "".join(" " + origin for origin in origins)
    edge = f" {edge_origin}" if edge_origin else ""
    media_edge = f" {media_origin}" if media_origin else ""
    fetchable = "".join(f" {o}" for o in sorted({edge_origin, media_origin} - {""}))
    return "; ".join(
        (
            "default-src 'self'",
            f"img-src 'self' data: blob:{edge}{hosts}",
            f"media-src 'self' blob:{media_edge}",
            "style-src 'self' 'unsafe-inline'",  # 首屏 CSS 是内联的，元素上也有 style 属性
            f"script-src 'self'{third_party}",
            "font-src 'self'",
            # iPhone 存相册要 fetch 边缘节点上的图片 / 视频
            f"connect-src 'self'{fetchable}{hosts}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        )
    )


CSP = build_csp(
    seo.ANALYTICS_HTML,
    relay.edge_origin() if relay.edge_img_enabled() else "",
    relay.edge_origin() if relay.edge_media_enabled() else "",
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

_BOT_UA = re.compile(r"bot|spider|crawl|slurp|fetch|curl|wget|python|http", re.I)
_UNCOUNTED_PAGES = ("/stats", "/test")


def _is_html(response: Response) -> bool:
    return response.headers.get("content-type", "").startswith("text/html")


def _excluded_from_stats(request: Request) -> bool:
    """不计入统计的：站长测试设备（/test 开过）、服务器自己的请求，以及从 GitHub issue 里
    「在线复现」点进来的（src=issue）——issue 是公开的，通知邮件的链接扫描、爬虫都会打开它，
    2026-10-02 一条测试反馈就这样被不认识的 IP 打开了 4 次，统计里快手成功率直接成了 0。"""
    if request.query_params.get("src") == "issue":
        return True
    return stats.ignored_ip(limits.client_ip(request)) or stats.is_test_cookie(request.cookies.get(stats.TEST_COOKIE))


def _record_page_view(request: Request, response: Response) -> None:
    path = request.url.path
    if response.status_code != 200 or path in _UNCOUNTED_PAGES:
        return
    if _BOT_UA.search(request.headers.get("user-agent", "")):
        return
    stats.record("view", limits.client_ip(request), source=path)


def _strip_trailing_slash(request: Request) -> Response | None:
    """/douyin/ → /douyin 用 301。框架自带的是 307（临时跳转），搜索引擎不会把两个地址并成一个。
    开头的多个斜杠也收成一个：//evil.com/ 不能跳成 //evil.com（那是跳去别的网站）。"""
    path = request.url.path
    if request.method not in ("GET", "HEAD") or path == "/" or not path.endswith("/"):
        return None
    query = request.url.query
    return RedirectResponse("/" + path.strip("/") + (f"?{query}" if query else ""), status_code=301)


async def site_headers(request: Request, call_next) -> Response:
    # 要在 call_next 之前设：接口里起的转换任务会继承这个「不计入」标记
    if stats.enabled() and _excluded_from_stats(request):
        stats.mute()

    try:
        response = _strip_trailing_slash(request) or await call_next(request)
    except RuntimeError as exc:
        # 响应头还没发出去用户就走了（视频代理等上游第一块数据时常见），框架报这个错。
        # 人都走了，没什么可记的，免得每次在日志里留一整段 traceback
        if str(exc) != "No response returned.":
            raise
        return Response(status_code=499)
    path = request.url.path
    is_page = request.method in ("GET", "HEAD") and _is_html(response) and not path.startswith("/api")
    if path.startswith("/static/"):
        # 静态资源带内容版本号，可以长期缓存
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif is_page:
        response.headers.setdefault("Cache-Control", "public, max-age=600")
        if stats.enabled() and request.method == "GET":
            _record_page_view(request, response)

    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if _is_html(response):
        response.headers.setdefault("Content-Security-Policy", CSP)
    return response
