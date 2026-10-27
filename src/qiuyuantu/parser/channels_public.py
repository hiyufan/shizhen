"""无账号的公益解析备用：限制调用速率，并与官方公开预览核对作品身份。"""

import asyncio
import time

import httpx

API = "https://api.bugpk.com/api/wxsph"


class PublicResolver:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.next_call = 0.0

    async def resolve(self, parser, share_url: str) -> dict:
        try:
            await asyncio.wait_for(self.lock.acquire(), 3)
        except asyncio.TimeoutError:
            return {}
        try:
            # 运营方公布 QPS=1；请求拥堵或服务失败时仍可保留官方封面结果。
            await asyncio.sleep(max(0.0, self.next_call - time.monotonic()))
            self.next_call = time.monotonic() + 1.05
            result = await parser.get_json(API, params={"url": share_url}, timeout=20)
            return result if isinstance(result, dict) else {}
        except (httpx.HTTPError, ValueError, TypeError):
            return {}
        finally:
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
