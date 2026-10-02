"""/api/proxy：把第三方直链转发给浏览器——补 Referer / UA、透传 Range、可选加下载头。

只转发带有效签名的地址（即 /api/parse 返回过的），不做开放代理。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import stats
from ..convert import net
from ..convert.net import headers_for, is_safe_url_async, safe_filename
from . import limits
from .auth import SITE_AUTH

router = APIRouter()

_PASSTHROUGH_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag")
_EXT_BY_TYPE = (("video", "mp4"), ("jpeg", "jpg"), ("png", "png"), ("webp", "webp"))
_TIMEOUT = httpx.Timeout(30, read=120)


class _StreamSlot:
    """同一 IP 同时转发的视频流有上限（长连接、一直占带宽）；图片不进这个池子。release 可以重复调。"""

    def __init__(self, ip: str, *, counted: bool) -> None:
        self._ip = ip
        self._held = counted
        if counted:
            limits.proxy_streams.acquire(ip)

    def release(self) -> None:
        if self._held:
            self._held = False
            limits.proxy_streams.release(self._ip)


async def _open_upstream(url: str, headers: dict[str, str]) -> httpx.Response:
    # 池化的客户端，连接归池子管，这里只负责关掉响应。播放器拖进度条会打一连串
    # Range 请求，每个都重新握手的话实测要 1457ms/次，复用后省掉握手和 TCP 慢启动
    client = net.safe_client(for_url=url, follow_redirects=True, timeout=_TIMEOUT)
    try:
        resp = await client.send(client.build_request("GET", url, headers=headers), stream=True)
    except net.UnsafeURL as err:
        raise HTTPException(400, "源站跳转到了不允许的地址") from err
    except httpx.HTTPError as err:
        raise HTTPException(502, f"拉取失败: {err.__class__.__name__}") from err
    if resp.status_code >= 400:
        await resp.aclose()
        raise HTTPException(resp.status_code, "源站拒绝了请求")
    return resp


def _response_headers(resp: httpx.Response) -> dict[str, str]:
    headers = {key: resp.headers[key] for key in _PASSTHROUGH_HEADERS if key in resp.headers}
    headers.setdefault("accept-ranges", "bytes")
    headers["cache-control"] = "private, max-age=3600"
    return headers


def _attachment(filename: str, content_type: str) -> str:
    """下载头：没给文件名就叫 media，没扩展名的按内容类型补上。"""
    ext = next((ext for key, ext in _EXT_BY_TYPE if key in content_type), "")
    name = filename or safe_filename("media", ext)
    if ext and not name.lower().endswith("." + ext) and "." not in name[-5:]:
        name += "." + ext
    return f"attachment; filename*=UTF-8''{quote(name)}"


def _cdn_name(url: str) -> str:
    """统计里只记 CDN 的主域名（douyinpic.com），不记具体地址。"""
    return ".".join((httpx.URL(url).host or "").rsplit(".", 2)[-2:])


async def _relay_body(resp: httpx.Response, slot: _StreamSlot) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.aiter_raw(1 << 16):
            yield chunk
    finally:
        await resp.aclose()
        slot.release()


@router.get("/api/proxy", dependencies=SITE_AUTH)
async def api_proxy(
    request: Request,
    url: str,
    filename: str = "",
    download: int = 0,
    sig: str = "",
    ip: str = Depends(limits.proxy_limit),
):
    if not net.verify(url, sig):
        raise HTTPException(403, "这个地址不是解析结果里的，拒绝转发")
    if not await is_safe_url_async(url):
        raise HTTPException(400, "不支持的地址")

    upstream_headers = headers_for(url)
    if byte_range := request.headers.get("range"):
        upstream_headers["Range"] = byte_range

    slot = _StreamSlot(ip, counted=not limits.looks_like_image(url))
    try:
        resp = await _open_upstream(url, upstream_headers)
    except BaseException:
        slot.release()
        raise
    # URL 没认出来但响应头说是图片，那也早点把槽还回去
    if resp.headers.get("content-type", "").startswith("image/"):
        slot.release()

    headers = _response_headers(resp)
    if download:
        headers["content-disposition"] = _attachment(filename, headers.get("content-type", ""))
        stats.record("download", ip, source=_cdn_name(url))
    return StreamingResponse(_relay_body(resp, slot), status_code=resp.status_code, headers=headers)
