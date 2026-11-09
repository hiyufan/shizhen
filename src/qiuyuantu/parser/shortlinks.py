"""通用短链服务（新浪 t.cn 等）：先跟到落地地址，再按落地地址找解析器。

平台自己的短链（v.douyin.com、b23.tv 等）由各自解析器处理，不在这里。
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx

from ..utils import create_async_client

HOSTS = {"t.cn", "sinaurl.cn", "bit.ly", "tinyurl.com", "is.gd", "ow.ly", "buff.ly", "cutt.ly", "rebrand.ly"}
MAX_HOPS = 4
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"


def is_short(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host.removeprefix("www.") in HOSTS


async def expand(url: str) -> str:
    """只跟跳转、不取正文；跳不动或请求失败时返回走到的最后一个地址，交给原来的流程报错。"""
    async with create_async_client(follow_redirects=False, timeout=10) as client:
        for _ in range(MAX_HOPS):
            if not is_short(url):
                break
            try:
                response = await client.get(url, headers={"User-Agent": _UA})
            except httpx.HTTPError:
                break
            location = response.headers.get("location")
            if not location:
                break
            url = urljoin(url, location)
    return url
