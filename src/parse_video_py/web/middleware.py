"""全站中间件：安全响应头、缓存头，以及页面浏览的统计。"""

from __future__ import annotations

import re

from fastapi import Request, Response

from .. import stats
from ..convert import relay
from . import limits

_IMG_SRC = "img-src 'self' data: blob:" + (f" {relay.edge_origin()}" if relay.edge_img_enabled() else "")
CSP = "; ".join(
    (
        "default-src 'self'",
        _IMG_SRC,
        "media-src 'self' blob:",
        "style-src 'self' 'unsafe-inline'",
        "script-src 'self' 'unsafe-inline'",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
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
    """站长测试设备（/test 开过）和服务器自己的请求不计入统计。"""
    return stats.ignored_ip(limits.client_ip(request)) or stats.is_test_cookie(request.cookies.get(stats.TEST_COOKIE))


def _record_page_view(request: Request, response: Response) -> None:
    path = request.url.path
    if response.status_code != 200 or path in _UNCOUNTED_PAGES:
        return
    if _BOT_UA.search(request.headers.get("user-agent", "")):
        return
    stats.record("view", limits.client_ip(request), source=path)


async def site_headers(request: Request, call_next) -> Response:
    # 要在 call_next 之前设：接口里起的转换任务会继承这个「不计入」标记
    if stats.enabled() and _excluded_from_stats(request):
        stats.mute()

    response = await call_next(request)
    path = request.url.path
    is_page = request.method == "GET" and _is_html(response) and not path.startswith("/api")
    if path.startswith("/static/"):
        # 静态资源带内容版本号，可以长期缓存
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif is_page:
        response.headers.setdefault("Cache-Control", "public, max-age=600")
        if stats.enabled():
            _record_page_view(request, response)

    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if _is_html(response):
        response.headers.setdefault("Content-Security-Policy", CSP)
    return response
