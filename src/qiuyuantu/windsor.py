"""站长报表的 Windsor 只读客户端；凭证不进入日志、错误响应或缓存。"""

from __future__ import annotations

import contextvars
import json
import logging

import httpx

from .utils import create_async_client

BASE_URL = "https://connectors.windsor.ai"
MAX_ROWS = 50_000
MAX_BYTES = 12 * 1024 * 1024
_private_request = contextvars.ContextVar("windsor_private_request", default=False)


class ReportError(Exception):
    """只携带固定错误码，不能保留带 API Key 的上游 URL / 响应。"""


class _PrivateHTTPLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _private_request.get()


_log_filter = _PrivateHTTPLog()


def _protect_logs() -> None:
    # Windsor 仅支持查询参数鉴权；httpx 的 INFO 和 httpcore 的 DEBUG 会记录 URL。
    # ContextVar 只屏蔽这次请求的网络日志，并发的解析 / 下载日志不受影响。
    names = {"httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy"}
    names.update(name for name in logging.Logger.manager.loggerDict if name.startswith("httpcore."))
    for name in names:
        logging.getLogger(name).addFilter(_log_filter)


def _status_error(status: int) -> str:
    if status in (401, 403):
        return "authorization"
    if status == 429:
        return "rate_limit"
    return "upstream"


def _decode(raw: bytes) -> tuple[list[dict], bool]:
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ReportError("invalid_data") from None
    if not isinstance(payload, dict) or payload.get("error"):
        raise ReportError("upstream")
    if payload.get("status") == "pending":
        raise ReportError("pending")
    rows = payload.get("data")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ReportError("invalid_data")
    if len(rows) > MAX_ROWS:
        raise ReportError("too_large")
    return rows, len(rows) == MAX_ROWS


async def fetch(connector: str, api_key: str, params: dict) -> tuple[list[dict], bool]:
    if connector not in ("searchconsole", "bing_webmaster"):
        raise ValueError("Unsupported search connector")
    _protect_logs()
    token = _private_request.set(True)
    query = {**params, "api_key": api_key, "_renderer": "json", "_max_rows": MAX_ROWS}
    try:
        # 不跟随重定向，避免把查询参数中的凭证发给另一个地址。
        async with (
            create_async_client(timeout=httpx.Timeout(90, connect=15), follow_redirects=False) as client,
            client.stream("GET", f"{BASE_URL}/{connector}", params=query) as response,
        ):
            if response.status_code == 202:
                raise ReportError("pending")
            if response.status_code != 200:
                raise ReportError(_status_error(response.status_code))
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise ReportError("too_large")
        return _decode(bytes(raw))
    except httpx.TimeoutException:
        raise ReportError("timeout") from None
    except (httpx.HTTPError, OSError):
        raise ReportError("network") from None
    finally:
        _private_request.reset(token)
