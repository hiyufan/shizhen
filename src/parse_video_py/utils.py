import asyncio
import concurrent.futures
import contextvars
import functools
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from urllib.parse import parse_qs, urlparse

import httpx

# 当前正在解析的平台（parser/__init__.py 里设置），用来决定走哪个代理
current_source: contextvars.ContextVar[str] = contextvars.ContextVar("current_source", default="")

# 这些平台对海外 / 机房 IP 不友好；海外部署时给它们单独配 PARSE_VIDEO_PROXY_CN
CN_SOURCES = {
    "douyin",
    "redbook",
    "kuaishou",
    "bilibili",
    "weibo",
    "xigua",
    "pipixia",
    "acfun",
    "weishi",
    "lvzhou",
    "zuiyou",
    "quanmin",
    "lishipin",
    "pipigaoxiao",
    "huya",
    "doupai",
    "meipai",
    "quanminkge",
    "sixroom",
    "xinpianchang",
    "haokan",
    "qqvideo",
    "sohu",
    "cctv",
}


# 国内平台的域名关键字：页面 / CDN 地址里带这些的，海外服务器访问时走国内代理
CN_HOST_KEYWORDS = ("bilibili", "b23.tv", "xiaohongshu", "douyin", "kuaishou", "weibo", "pipix", "ixigua", "acfun")


def is_cn_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(k in host for k in CN_HOST_KEYWORDS)


def proxy_for(source: str | None = None) -> str | None:
    """PARSE_VIDEO_PROXY_CN 只给国内平台用，PARSE_VIDEO_PROXY 给所有平台兜底。"""
    source = source if source is not None else current_source.get()
    if source in CN_SOURCES and os.getenv("PARSE_VIDEO_PROXY_CN"):
        return os.getenv("PARSE_VIDEO_PROXY_CN")
    return os.getenv("PARSE_VIDEO_PROXY") or None


# YouTube 对机房 IP 按视频抽查「登录确认你不是机器人」，cookies、PO Token 都不管用，换个出口就过。
# PARSE_VIDEO_FALLBACK_PROXY 一般指向 docker-compose 里的 warp 服务（Cloudflare WARP）：
# 平时直连，只有被抽查到的才绕一下
_BOT_CHECK = re.compile(r"confirm you.{0,3}re not a bot", re.I)


def fallback_proxy() -> str:
    return os.getenv("PARSE_VIDEO_FALLBACK_PROXY", "").strip()


def wants_fallback(err: BaseException) -> bool:
    return bool(fallback_proxy()) and bool(_BOT_CHECK.search(str(err)))


URL_REG = re.compile(r"http[s]?:\/\/[\w.-]+[\w\/-]*[\w.-]*\??[\w=&:\-\+\%.]*[/]*")


def extract_url(text: str) -> str | None:
    """从文本中提取第一个匹配的 URL"""
    match = URL_REG.search(text)
    return match.group() if match else None


def get_val_from_url_by_query_key(url: str, query_key: str) -> str:
    """
    从url的query参数中解析出query_key对应的值
    :param url: url地址
    :param query_key: query参数的key
    :return:
    """
    url_res = urlparse(url)
    url_query = parse_qs(url_res.query, keep_blank_values=True)

    if query_key not in url_query:
        raise KeyError(f"url中不存在query参数: {query_key}")
    query_val = url_query[query_key][0]
    if not query_val:
        raise ValueError(f"url中query参数值长度为0: {query_key}")
    return query_val


def create_async_client(**kwargs) -> httpx.AsyncClient:
    """创建 httpx.AsyncClient，自动注入代理配置。

    从环境变量 PARSE_VIDEO_PROXY 读取代理地址（如 http://user:pass@host:port），
    未设置则不使用代理。其余参数透传给 httpx.AsyncClient。
    """
    # 解析器会跟着用户给的短链跳转, 每一跳都做 SSRF 检查
    from .convert import relay
    from .convert.net import safe_client

    if current_source.get() in CN_SOURCES and relay.enabled() and "transport" not in kwargs:
        # 海外服务器 + 边缘函数中继：国内平台的请求由中继代发。
        # 用单例, 每次新建会连带新建一个到中继的连接池, 白白多握手一次
        kwargs["transport"] = relay.shared_transport()
    else:
        proxy = proxy_for()
        if proxy and "proxy" not in kwargs:
            kwargs["proxy"] = proxy
    return safe_client(**kwargs)


async def gather_or_cancel(*aws: Awaitable) -> list:
    """同 asyncio.gather，但有一个失败就把其余的取消掉再抛（gather 会让它们接着跑，往已经删掉的工作目录里写）。
    抛出去的是原来那个异常，任务的报错文字不变。"""
    futures = [asyncio.ensure_future(aw) for aw in aws]
    try:
        return await asyncio.gather(*futures)
    except BaseException:
        for fut in futures:
            fut.cancel()
        await asyncio.gather(*futures, return_exceptions=True)
        raise


# yt-dlp 单独一个线程池。asyncio 默认的池只有 CPU 数 + 4 个线程（线上 4 核 = 8 个），httpx 建连时的
# DNS 查询（loop.getaddrinfo）、统计落盘、转图也都排在里面；yt-dlp 下载一占几分钟、解析一占几秒，
# 几个 YouTube 下载 + 解析就能把它占满，之后全站的 DNS 查询排队，所有解析和转发跟着变慢。
# 线程按需创建，空着不占资源
_YTDLP_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="yt-dlp")

T = TypeVar("T")


async def run_ytdlp(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """同 asyncio.to_thread，只是放进 yt-dlp 自己的线程池。contextvars 照样带过去（proxy_for 要看 current_source）。"""
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_YTDLP_POOL, call)
