"""通过边缘函数中继请求（阿里云 ESA / 腾讯 EdgeOne 等）——给没有国内 HTTP 代理的海外服务器用。

协议（和 scripts/esa-relay.js 对应）：
    POST {RELAY}?url=<目标地址>
    x-relay-token / x-relay-method / x-relay-headers(base64 JSON)   body = 原请求体
  ← 200, body = 目标响应体, x-relay-status = 目标状态码, x-relay-headers = base64 JSON [[k, v], ...]

做成 httpx 的 transport，解析器代码一行不用改；跳转由 httpx 在本地处理，每一跳都过 SSRF 检查。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os

import httpx

RELAY_URL = os.environ.get("PARSE_VIDEO_RELAY_CN", "").strip()
RELAY_TOKEN = os.environ.get("PARSE_VIDEO_RELAY_TOKEN", "").strip()

_DROP = {"host", "content-length", "connection", "accept-encoding"}


def enabled() -> bool:
    return bool(RELAY_URL and RELAY_TOKEN)


class RelayTransport(httpx.AsyncBaseTransport):
    def __init__(self, relay_url: str = RELAY_URL, token: str = RELAY_TOKEN, timeout: float = 40.0):
        self.relay_url = relay_url
        self.token = token
        # 中继地址是固定的一个域名, 连接留着重复用。httpx 默认 keepalive 只保 5 秒,
        # 解析请求零零散散地来, 5 秒一过连接就没了, 每次都要重做 DNS+TCP+TLS(实测 642ms)
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=300.0),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP}
        relay_headers = {
            "x-relay-token": self.token,
            "x-relay-method": request.method,
            "x-relay-headers": base64.b64encode(json.dumps(headers, ensure_ascii=False).encode("utf-8")).decode(),
            "content-type": "application/octet-stream",
        }
        resp = await self._client.post(self.relay_url, params={"url": str(request.url)}, headers=relay_headers,
                                       content=body)
        if resp.status_code != 200:
            raise httpx.TransportError(f"中继返回 {resp.status_code}: {resp.text[:120]}", request=request)
        status = int(resp.headers.get("x-relay-status", "599"))
        if status == 599:
            raise httpx.ConnectError(f"中继访问目标失败: {resp.headers.get('x-relay-error', '')}", request=request)
        try:
            pairs = json.loads(base64.b64decode(resp.headers.get("x-relay-headers", "e30=")).decode("utf-8"))
        except Exception:  # noqa: BLE001
            pairs = []
        return httpx.Response(status, headers=[(k, v) for k, v in pairs], content=resp.content, request=request)

    async def ping(self) -> None:
        """戳一下中继本身，不产生任何出站请求。

        带 token 但不带 url 的请求会被中继直接 400 回来（见 esa-relay.js 的
        relay()），只走一个到边缘节点的往返，是最省的保活方式。
        """
        await self._client.post(
            self.relay_url, params={"url": ""},
            headers={"x-relay-token": self.token, "x-relay-method": "GET"}, content=b"",
        )

    async def aclose(self) -> None:
        await self._client.aclose()


_shared: RelayTransport | None = None


def shared_transport() -> RelayTransport:
    """全局共用一个中继 transport。

    以前每次 create_async_client() 都 new 一个 RelayTransport, 每个都自带一个
    AsyncClient, 于是每次解析调用都要重新对中继握手。单例之后一次 B站 解析
    实测从 1098ms/次降到 268ms/次。
    """
    global _shared
    if _shared is None:
        _shared = RelayTransport()
    return _shared


async def keepalive(interval: float = 60.0) -> None:
    """定期戳中继，把那条跨洋连接焐着。

    实测（美国机房 -> 阿里云 ESA）：冷连接 1305ms，热连接 184ms，差的是一次
    跨太平洋的 TCP+TLS 握手。空闲 70 秒连接还在，130 秒就凉了，中继那端的
    idle timeout 在两者之间。

    解析请求零零散散地来，不保活的话大部分用户都正好撞在冷连接上，平白多等
    1.3 秒——站点流量越小，撞中的比例越高。每分钟一个 400 空响应换掉这 1.3 秒，
    很划算。
    """
    tr = shared_transport()
    while True:
        with contextlib.suppress(Exception):   # 保活失败就等下一轮, 别影响主服务
            await tr.ping()
        await asyncio.sleep(interval)


async def aclose_shared() -> None:
    global _shared
    if _shared is not None:
        await _shared.aclose()
        _shared = None
