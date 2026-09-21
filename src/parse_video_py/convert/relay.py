"""通过边缘函数中继请求（阿里云 ESA / 腾讯 EdgeOne 等）——给没有国内 HTTP 代理的海外服务器用。

协议（和 scripts/esa-relay.js 对应）：
    POST {RELAY}?url=<目标地址>
    x-relay-token / x-relay-method / x-relay-headers(base64 JSON)   body = 原请求体
  ← 200, body = 目标响应体, x-relay-status = 目标状态码, x-relay-headers = base64 JSON [[k, v], ...]

做成 httpx 的 transport，解析器代码一行不用改；跳转由 httpx 在本地处理，每一跳都过 SSRF 检查。
"""
from __future__ import annotations

import base64
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
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)

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

    async def aclose(self) -> None:
        await self._client.aclose()
