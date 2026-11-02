"""无账号的公益解析备用：限制调用速率，并与官方公开预览核对作品身份。

视频号把视频地址挡在登录态 token 后面，匿名访问官方接口只拿得到封面。这里不碰账号、不碰
客户端，只调「分享链接 → 直链」的公开解析服务。单一服务会挂（限流、停服），所以地址可用
`PARSE_VIDEO_CHANNELS_RESOLVERS` 配成多个、逗号分隔，按顺序试到第一个通过身份校验的为止；
每个结果仍由调用方用官方预览的标题 / 作者和媒体域名白名单兜底，换哪个服务都塞不进错视频。
"""

import asyncio
import os
import time

import httpx

from ..utils import current_source

_DEFAULT = "https://api.bugpk.com/api/wxsph"
_MAX_ENDPOINTS = 4


def endpoints() -> list[str]:
    raw = os.getenv("PARSE_VIDEO_CHANNELS_RESOLVERS", "").strip()
    urls = [u.strip() for u in raw.split(",") if u.strip()] if raw else [_DEFAULT]
    return [u for u in urls if u.startswith("https://")][:_MAX_ENDPOINTS]


class PublicResolver:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.next_call = 0.0

    async def resolve(self, parser, share_url: str, accept) -> dict:
        """逐个公开服务请求，返回首个 accept(结果) 为真的响应；都不行返回 {}。"""
        try:
            await asyncio.wait_for(self.lock.acquire(), 3)
        except asyncio.TimeoutError:
            return {}
        # 公益接口不在国内平台之列，直连就行。沿用视频号的来源会走国内中继，它常要 5～10 秒才返回，
        # 中继约 10 秒就回 504，结果一半的作品只剩封面。
        token = current_source.set("")
        try:
            for endpoint in endpoints():
                # 运营方公布 QPS=1；拥堵或失败就换下一个，最终拿不到仍可保留官方封面。
                await asyncio.sleep(max(0.0, self.next_call - time.monotonic()))
                self.next_call = time.monotonic() + 1.05
                try:
                    result = await parser.get_json(endpoint, params={"url": share_url}, timeout=20)
                except (httpx.HTTPError, ValueError, TypeError):
                    continue
                if isinstance(result, dict) and accept(result):
                    return result
            return {}
        finally:
            current_source.reset(token)
            self.lock.release()


resolver = PublicResolver()


def same_post(official: dict, result: dict) -> bool:
    if result.get("code") != 200 or not isinstance(result.get("data"), dict):
        return False
    data = result["data"]
    if not isinstance(data.get("author"), dict) or not isinstance(data.get("title"), str):
        return False
    feed, author = official.get("feedInfo") or {}, official.get("authorInfo") or {}
    name = (data.get("author") or {}).get("name") or ""
    expected_title = " ".join((feed.get("description") or "").split())
    received_title = " ".join((data.get("title") or "").split())
    return (
        bool(expected_title)
        and expected_title == received_title
        and bool(author.get("nickname"))
        and name == author["nickname"]
    )
