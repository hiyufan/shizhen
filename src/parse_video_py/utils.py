import contextvars
import os
import re
from urllib.parse import parse_qs, urlparse

import httpx

# 当前正在解析的平台（parser/__init__.py 里设置），用来决定走哪个代理
current_source: contextvars.ContextVar[str] = contextvars.ContextVar("current_source", default="")

# 这些平台对海外 / 机房 IP 不友好；海外部署时给它们单独配 PARSE_VIDEO_PROXY_CN
CN_SOURCES = {"douyin", "redbook", "kuaishou", "bilibili", "weibo", "xigua", "pipixia", "acfun", "weishi",
              "lvzhou", "zuiyou", "quanmin", "lishipin", "pipigaoxiao", "huya", "doupai", "meipai",
              "quanminkge", "sixroom", "xinpianchang", "haokan", "qqvideo", "sohu", "cctv"}


def proxy_for(source: str | None = None) -> str | None:
    """PARSE_VIDEO_PROXY_CN 只给国内平台用，PARSE_VIDEO_PROXY 给所有平台兜底。"""
    source = source if source is not None else current_source.get()
    if source in CN_SOURCES and os.getenv("PARSE_VIDEO_PROXY_CN"):
        return os.getenv("PARSE_VIDEO_PROXY_CN")
    return os.getenv("PARSE_VIDEO_PROXY") or None

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

    try:
        query_val = url_query[query_key][0]
    except KeyError:
        raise KeyError(f"url中不存在query参数: {query_key}")

    if len(query_val) == 0:
        raise ValueError(f"url中query参数值长度为0: {query_key}")

    return url_query[query_key][0]


def create_async_client(**kwargs) -> httpx.AsyncClient:
    """创建 httpx.AsyncClient，自动注入代理配置。

    从环境变量 PARSE_VIDEO_PROXY 读取代理地址（如 http://user:pass@host:port），
    未设置则不使用代理。其余参数透传给 httpx.AsyncClient。
    """
    # 解析器会跟着用户给的短链跳转, 每一跳都做 SSRF 检查
    from .convert import relay
    from .convert.net import safe_client

    if current_source.get() in CN_SOURCES and relay.enabled() and "transport" not in kwargs:
        # 海外服务器 + 边缘函数中继：国内平台的请求由中继代发
        kwargs["transport"] = relay.RelayTransport()
    else:
        proxy = proxy_for()
        if proxy and "proxy" not in kwargs:
            kwargs["proxy"] = proxy
    return safe_client(**kwargs)
