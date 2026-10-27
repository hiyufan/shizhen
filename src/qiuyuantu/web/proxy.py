"""/api/proxy：把第三方直链转发给浏览器——补 Referer / UA、透传 Range、可选加下载头。

只转发带有效签名的地址（即 /api/parse 返回过的），不做开放代理。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import stats
from ..convert import net, signal, youtube_native
from ..convert.net import headers_for, is_safe_url_async, safe_filename
from ..parser.animation import file_kind
from . import limits
from .auth import SITE_AUTH

router = APIRouter()

# 响应体按原始字节转发，编码头也必须保留；否则压缩传输的 JSON 等会被下载为不可读的内容。
_PASSTHROUGH_HEADERS = (
    "content-type",
    "content-encoding",
    "content-length",
    "content-range",
    "accept-ranges",
    "last-modified",
    "etag",
)
_EXT_BY_TYPE = (
    ("video/webm", "webm"),
    ("video/quicktime", "mov"),
    ("video", "mp4"),
    ("jpeg", "jpg"),
    ("png", "png"),
    ("webp", "webp"),
    ("gif", "gif"),
    ("avif", "avif"),
    ("heic", "heic"),
)
_IMAGE_SUFFIXES = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
_TIMEOUT = httpx.Timeout(30, read=120)


class _StreamSlot:
    """同时转发的视频流有上限（长连接、一直占带宽）：每个 IP 一份、全站一份；图片不进这个池子。
    release 可以重复调。"""

    def __init__(self, ip: str, *, counted: bool) -> None:
        self._ip = ip
        self._held = False
        if not counted:
            return
        limits.proxy_streams.acquire(ip)
        try:
            limits.proxy_streams_global.acquire(limits.GLOBAL)
        except BaseException:
            limits.proxy_streams.release(ip)
            raise
        self._held = True

    def release(self) -> None:
        if self._held:
            self._held = False
            limits.proxy_streams.release(self._ip)
            limits.proxy_streams_global.release(limits.GLOBAL)


class _CachedVideoResponse(FileResponse):
    """本地兜底文件照样透传 Range，并按实际发送字节计入代理配额。"""

    def __init__(self, path: str, slot: _StreamSlot, ip: str, headers: dict):
        super().__init__(path, media_type="video/mp4", headers=headers)
        self.slot, self.ip = slot, ip

    async def __call__(self, scope, receive, send):
        async def counted(message):
            if message["type"] == "http.response.body":
                limits.proxy_daily.add(self.ip, len(message.get("body", b"")))
            await send(message)

        # pathsend 会绕过 body 回调；此处需要逐块计量和释放名额。
        scope = scope | {
            "extensions": {k: v for k, v in scope.get("extensions", {}).items() if k != "http.response.pathsend"}
        }
        try:
            await super().__call__(scope, receive, counted)
        finally:
            self.slot.release()


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
    content_type = content_type.lower()
    ext = next((ext for key, ext in _EXT_BY_TYPE if key in content_type), "")
    name = filename or safe_filename("media", ext)
    stem, dot, suffix = name.rpartition(".")
    if content_type.startswith("image/") and ext:
        name = f"{stem}.{ext}" if dot and suffix.lower() in _IMAGE_SUFFIXES else f"{name}.{ext}"
    elif ext and not name.lower().endswith("." + ext) and "." not in name[-5:]:
        name += "." + ext
    return f"attachment; filename*=UTF-8''{quote(name)}"


def _cdn_name(url: str) -> str:
    """统计里只记 CDN 的主域名（douyinpic.com），不记具体地址。"""
    return ".".join((httpx.URL(url).host or "").rsplit(".", 2)[-2:])


async def _relay_body(resp: httpx.Response, slot: _StreamSlot, ip: str) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.aiter_raw(1 << 16):
            limits.proxy_daily.add(ip, len(chunk))
            yield chunk
    finally:
        # 用户关掉页面时这里是在「已取消」的状态下跑的：await 一挂起就会再被取消一次。
        # 以前先 await aclose 再还名额，名额就永远还不回去（每个 IP 4 路、全站 64 路，漏满要等重启）
        slot.release()
        with anyio.move_on_after(5, shield=True):
            await resp.aclose()


def _outside_page(request: Request, download: int) -> bool:
    """浏览器 UA、不带 Referer、又不是下载的代理请求：网页里的播放 / 预览都带 Referer。
    2026-10-18 推特批量下载脚本被每日流量上限挡住后换着 IP（台湾的代理）继续拉，几千次请求全是这样；
    真人不带 Referer 的只有 OPPO / vivo 浏览器自带的下载器接手下载，那些都带 download=1。"""
    ua = request.headers.get("user-agent", "")
    return not download and not request.headers.get("referer") and ua.startswith("Mozilla/")


def _cached_video(path: str, filename: str, download: int, url: str, ip: str):
    headers = {"cache-control": "private, max-age=3600"}
    if download:
        headers["content-disposition"] = _attachment(filename, "video/mp4")
        stats.record_download(ip, url, source="youtube")
    return _CachedVideoResponse(path, _StreamSlot(ip, counted=True), ip, headers)


async def _signal_sticker(url: str, filename: str, download: int, ip: str):
    try:
        sticker = await signal.read_asset(url)
    except ValueError as exc:
        raise HTTPException(410, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "贴纸暂时无法读取，请稍后重试") from exc
    limits.proxy_daily.add(ip, len(sticker.content))
    headers = {"cache-control": "private, max-age=600"}
    if download:
        headers["content-disposition"] = _attachment(filename, sticker.media_type)
        stats.record_download(ip, url, source="signal")
    return Response(sticker.content, media_type=sticker.media_type, headers=headers)


async def _validate_proxy(request: Request, url: str, sig: str, download: int):
    if not net.verify(url, sig):
        raise HTTPException(403, "这个地址不是解析结果里的，拒绝转发")
    if _outside_page(request, download):
        raise HTTPException(403, "请在网页里打开或下载")
    cached = youtube_native.source_for_asset(url)
    if youtube_native.is_asset_url(url) and not cached:
        raise HTTPException(410, "原视频已过期，请重新解析")
    if not cached and not await is_safe_url_async(url):
        raise HTTPException(400, "不支持的地址")
    return cached


def _proxy_headers(resp: httpx.Response, url: str, filename: str, download: int, ip: str) -> dict:
    headers = _response_headers(resp)
    if file_kind(url):
        # 公开动画链接可来自任意公网主机，不能让伪装成动画的 HTML / JS 成为本站脚本源。
        headers["content-type"] = "application/octet-stream"
        headers["content-disposition"] = _attachment(filename, "application/octet-stream")
        headers["content-security-policy"] = "sandbox; default-src 'none'"
        headers["x-content-type-options"] = "nosniff"
    if download:
        headers["content-disposition"] = _attachment(filename, headers.get("content-type", ""))
        stats.record_download(ip, url, source=_cdn_name(url))
    return headers


@router.get("/api/proxy", dependencies=SITE_AUTH)
async def api_proxy(
    request: Request,
    url: str,
    filename: str = "",
    download: int = 0,
    sig: str = "",
    ip: str = Depends(limits.proxy_limit),
):
    cached = await _validate_proxy(request, url, sig, download)
    limits.proxy_daily.check(ip)

    if cached:
        return _cached_video(cached.path, filename, download, url, ip)

    if signal.is_asset_url(url):
        return await _signal_sticker(url, filename, download, ip)

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

    headers = _proxy_headers(resp, url, filename, download, ip)
    return StreamingResponse(_relay_body(resp, slot, ip), status_code=resp.status_code, headers=headers)


class DownloadHit(BaseModel):
    url: str = Field(max_length=4000)
    sig: str = Field(max_length=64)


@router.post("/api/download-hit", dependencies=SITE_AUTH)
async def api_download_hit(hit: DownloadHit, ip: str = Depends(limits.proxy_limit)):
    """视频 / 音频从国内边缘节点下载时不经过这台服务器，页面单独报一声，使用统计里的「下载」才不会少。
    只认解析结果里签过名的地址，别人随便刷也只能刷到自己解析过的东西。"""
    if not net.verify(hit.url, hit.sig):
        raise HTTPException(403, "这个地址不是解析结果里的")
    stats.record_download(ip, hit.url, source=_cdn_name(hit.url))
    return {"ok": True}
