"""独立下载域名（PARSE_VIDEO_DOWNLOAD_ORIGIN）：让大文件下载走一条全新的连接。

桌面浏览器点同源的下载链接，复用的是页面那条 HTTP/2 连接。这条连接加载页面和缩略图时被客户端的接收窗口
顶住过，拥塞控制判定「带宽已探明」退出了启动阶段，之后只能慢慢涨：往返 200ms 的线路上要 30 秒以上才到满速，
几十 MB 的文件大半时间耗在爬坡上（2026-11 实测，同一条线路全新连接 181 MB 用 17 秒）。换一个域名就是一条新连接。

部署时这个域名要用**单独的证书**：证书同时包含主站域名、又解析到同一个地址的话，浏览器会把两个域名合并到
同一条 HTTP/2 连接上，等于没换。

这个域名上只放行下载用的几个 GET 接口，其它地址跳回主站。它收不到主站的 Cookie（访问来源、交互证据、
站长测试标记都在里面），所以这里的请求一律不记统计，由页面在主站另报一声
（/api/download-hit、/api/jobs/<id>/download-hit）。下载链接是跨域的，出错时浏览器会离开结果页，
所以错误回成一页带原因的说明，而不是一段 JSON。
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import seo
from .auth import SITE_AUTH
from .preferences import preferences, translate

log = logging.getLogger("uvicorn.error")

# 任务产物、原视频、转发的直链：都是点一下就交给浏览器下载管理器的整文件
_DOWNLOAD_PATH = re.compile(r"/api/(?:proxy|jobs/[\w-]+/file|source/[\w-]+)")
_CJK = re.compile(r"[\u3400-\u9fff]")
GENERIC_ERROR = "下载没有成功，回到页面再试一次。"


def _origin() -> str:
    raw = os.environ.get("PARSE_VIDEO_DOWNLOAD_ORIGIN", "").strip().rstrip("/")
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.username:
        log.warning("PARSE_VIDEO_DOWNLOAD_ORIGIN 要写成 https://域名，已忽略：%r", raw)
        return ""
    if parts.netloc.lower() == urlsplit(seo.SITE_URL).netloc.lower():
        log.warning("PARSE_VIDEO_DOWNLOAD_ORIGIN 不能和主站是同一个域名，已忽略")
        return ""
    if SITE_AUTH:
        # 浏览器不会把主站的 Basic Auth 凭证带到另一个域名，下载时会再弹一次登录框
        log.warning("开了整站登录（PARSE_VIDEO_USERNAME）时不使用独立下载域名")
        return ""
    return raw


ORIGIN = _origin()
HOST = urlsplit(ORIGIN).netloc.lower()


def is_request(request: Request) -> bool:
    return bool(HOST) and request.headers.get("host", "").lower() == HOST


def refusal(request: Request) -> Response | None:
    """下载域名上不是下载接口的请求：页面类的跳回主站，其余当不存在。主站域名上的请求返回 None。"""
    if not is_request(request):
        return None
    path = request.url.path
    if request.method in ("GET", "HEAD") and _DOWNLOAD_PATH.fullmatch(path):
        return None
    if request.method in ("GET", "HEAD") and seo.SITE_URL:
        query = request.url.query
        return RedirectResponse(seo.SITE_URL + "/" + path.lstrip("/") + (f"?{query}" if query else ""), 301)
    return Response(status_code=404)


def _message(detail: object, locale: str) -> str:
    """接口的中文原因直接给中文页面；英文页面只用有译文的，带数值的提示（等几秒之类）退回通用说法。"""
    text = detail if isinstance(detail, str) and _CJK.search(detail) else GENERIC_ERROR
    if locale != "en":
        return text
    translated = translate(text, "en")
    return translate(GENERIC_ERROR, "en") if _CJK.search(translated) else translated


async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
    """主站照旧回 JSON；下载域名上是浏览器整页打开的，回一页说明。"""
    if not is_request(request):
        return await http_exception_handler(request, exc)
    from .rendering import templates

    locale = preferences(request)["locale"]
    context = {"locale": locale, "message": _message(exc.detail, locale), "site_url": seo.SITE_URL}
    response = templates.TemplateResponse(
        request=request, name="download_error.html", context=context, status_code=exc.status_code, headers=exc.headers
    )
    response.headers["Cache-Control"] = "no-store"
    return response
