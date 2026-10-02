"""组装 FastAPI 应用：后台任务的启停、中间件、静态文件和各组路由。"""

from __future__ import annotations

import asyncio
import contextlib
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .. import failures, feedback, stats
from ..convert import config, jobs, net, relay, store, updater
from ..parser import douyin, parse_video_share_url
from ..utils import extract_url
from . import admin, media, pages, parse, proxy, upstream
from .middleware import site_headers
from .rendering import STATIC_DIR

SWEEP_INTERVAL = 300


def _cleanup() -> None:
    """过期任务 / 原视频、没登记在册的孤儿文件、磁盘配额；统计只留最近几个月，失败链接只留 7 天。"""
    jobs.sweep()
    store.sweep()
    known = jobs.known_paths() | store.known_paths()
    store.sweep_orphans(known)
    store.enforce_quota(known)
    failures.prune()
    if stats.enabled():
        stats.prune()


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        with contextlib.suppress(Exception):
            _cleanup()


async def _reparse(link: str):
    """反馈的复测：直接调解析器，不走接口、不进统计。"""
    return await asyncio.wait_for(parse_video_share_url(extract_url(link) or link), parse.PARSE_TIMEOUT)


def _background_jobs() -> list:
    """进程活着期间一直跑的后台任务，按配置决定开哪些。"""
    coros = [
        _sweeper(),
        # 抖音图文兜底的常驻 Chromium 在后台起好 + 过掉首次人机验证，
        # 第一个解析图文的用户不用垫冷启动的十几秒
        douyin.warmup_browser(),
    ]
    if relay.enabled():
        # 中继连接空闲两分钟就凉，重连要 1.3 秒。保活任务进去先戳一下，
        # 顺带把进程启动后的第一条连接也预热了，第一个用户不用替后面的人垫这 1.3 秒
        coros.append(relay.keepalive())
    if stats.enabled():
        coros.append(stats.flusher())
    if config.YTDLP_AUTOUPDATE_DAYS > 0:
        coros.append(updater.loop(config.YTDLP_AUTOUPDATE_DAYS))
    if feedback.enabled():
        # 反馈：同步 GitHub issue、issue 关了先复测再发邮件
        coros.append(feedback.loop(_reparse))
    return coros


async def _shutdown() -> None:
    # 池化的客户端 aclose() 是空操作，退出时在这里真关
    for closer in (net.aclose_pool, relay.aclose_shared, douyin.aclose_browser):
        with contextlib.suppress(Exception):
            await closer()


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    config.ensure_dirs()
    # 任务和原视频的登记在内存里，上次进程留下的文件启动时先清一遍
    with contextlib.suppress(Exception):
        _cleanup()
    running = [asyncio.create_task(coro) for coro in _background_jobs()]
    yield
    for task in running:
        task.cancel()
    await _shutdown()


def _mount_mcp(app: FastAPI):
    """MCP 是上游的可选功能；公开部署建议 PARSE_VIDEO_MCP=0 关掉，少一个暴露面。"""
    if os.environ.get("PARSE_VIDEO_MCP", "1") != "1":
        return None
    try:
        from fastapi_mcp import FastApiMCP
    except ImportError:  # pragma: no cover - 可选依赖
        return None
    mcp = FastApiMCP(app)
    mcp.mount_http()
    return mcp


def create_app() -> FastAPI:
    app = FastAPI(lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.middleware("http")(site_headers)
    mcp = _mount_mcp(app)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for module in (upstream, parse, proxy, media, admin):
        app.include_router(module.router)
    # 放在最后：/{slug} 是兜底路由，不能抢在 /robots.txt /sitemap.xml 前面
    app.include_router(pages.router)
    if mcp is not None:
        mcp.setup_server()
    return app
