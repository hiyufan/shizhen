"""站长用的：健康检查、使用统计（/stats）、测试模式（/test，开了之后这台设备不计入统计）。"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import failures, stats
from ..convert import config, jobs, store, updater
from . import limits, parse
from .auth import require_stats_enabled, require_stats_token
from .rendering import PRIVATE_PAGE_HEADERS, page_context, render

router = APIRouter()

# 时间范围 -> (跨度, 每格多长)，单位秒
STATS_RANGES = {
    "24h": (86400, 3600),
    "7d": (7 * 86400, 86400),
    "30d": (30 * 86400, 86400),
    "90d": (90 * 86400, 86400),
}
_MAX_TZ_OFFSET = 14 * 3600
_TEST_COOKIE_AGE = 365 * 86400


@router.get("/api/health")
async def api_health():
    """给负载均衡 / 监控用。"""
    return {
        "ok": True,
        "jobs": jobs.stats(),
        "disk_used": store.disk_usage(),
        "disk_quota": config.DISK_QUOTA_BYTES,
        "ytdlp": updater.current_version(),
        "pot": bool(config.POT_URL),
        "cache": len(parse.cache),
    }


# --------------------------------------------------------------------------- 使用统计


@router.get("/api/stats", dependencies=[Depends(require_stats_token)])
async def api_stats(range: str = "24h", tz: int = 0):  # noqa: A002 - 查询参数名是对外接口
    """按时间分桶的使用量：浏览 / 解析 / 任务 / 下载 / 人数，以及各平台成功率。"""
    span, step = STATS_RANGES.get(range, STATS_RANGES["24h"])
    # JS 的 getTimezoneOffset 是「UTC 减本地」的分钟数
    tz_offset = max(-_MAX_TZ_OFFSET, min(_MAX_TZ_OFFSET, -tz * 60))
    now = time.time()
    since = now - span
    since -= (since + tz_offset) % step  # 对齐到桶的起点，最左一格才是完整的
    await asyncio.to_thread(stats.flush)
    data = await asyncio.to_thread(stats.summary, since, now, step, tz_offset)
    data["failures"] = await asyncio.to_thread(failures.recent, max(since, now - failures.RETENTION_DAYS * 86400))
    data["range"] = range
    return data


@router.get("/stats", response_class=HTMLResponse, dependencies=[Depends(require_stats_token)])
async def stats_page(request: Request, token: str = ""):
    ctx = page_context(request, title="使用统计 - 拾帧", path="/stats")
    ctx.update({"ranges": list(STATS_RANGES), "token": token})
    return render(request, "stats.html", ctx, headers=PRIVATE_PAGE_HEADERS)


# --------------------------------------------------------------------------- 测试模式


def _secure_cookie(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _test_page(request: Request, *, on: bool, error: str = ""):
    ctx = page_context(request, title="测试模式 - 拾帧", path="/test")
    ctx.update({"test_on": on, "test_error": error})
    return render(request, "test.html", ctx, status_code=403 if error else 200, headers=PRIVATE_PAGE_HEADERS)


@router.get("/test", response_class=HTMLResponse, dependencies=[Depends(require_stats_enabled)])
async def test_mode(request: Request, off: int = 0):
    """站长测试模式：用统计口令开一次，这台设备之后的访问、解析、下载、转换都不计入统计。
    口令走 POST 表单，不进网址（免得留在浏览器历史和访问日志里）。"""
    if not off:
        return _test_page(request, on=stats.is_test_cookie(request.cookies.get(stats.TEST_COOKIE)))
    response = _test_page(request, on=False)
    response.delete_cookie(stats.TEST_COOKIE)
    response.delete_cookie(stats.TEST_FLAG_COOKIE)
    return response


@router.post(
    "/test",
    response_class=HTMLResponse,
    dependencies=[Depends(require_stats_enabled), Depends(limits.parse_limit)],
)
async def test_mode_on(request: Request):
    token = str((await request.form()).get("token") or "")
    if not stats.check_token(token):
        return _test_page(request, on=False, error="口令不对")
    response = _test_page(request, on=True)
    secure = _secure_cookie(request)
    response.set_cookie(
        stats.TEST_COOKIE,
        stats.test_cookie_value(),
        max_age=_TEST_COOKIE_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    # 给页面上的「测试模式」小标记看的，不参与判断
    response.set_cookie(stats.TEST_FLAG_COOKIE, "1", max_age=_TEST_COOKIE_AGE, secure=secure, samesite="lax")
    return response
