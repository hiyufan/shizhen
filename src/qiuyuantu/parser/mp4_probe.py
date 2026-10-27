"""有界读取 MP4 元数据；请求的清晰度可能被平台降档，以文件里的轨道信息为准。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from pathlib import Path

import httpx

from ..convert import relay
from ..convert.net import safe_client
from .base import FormatInfo
from .quality import short_side

_HEAD_BYTES = 1024 * 1024
_TAIL_BYTES = 512 * 1024
_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)\Z")


def _boxes(data: memoryview) -> Iterator[tuple[bytes, memoryview]]:
    offset = 0
    for _ in range(2048):
        if offset + 8 > len(data):
            break
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind, header = bytes(data[offset + 4 : offset + 8]), 8
        if size == 1:
            if offset + 16 > len(data):
                break
            size, header = int.from_bytes(data[offset + 8 : offset + 16], "big"), 16
        if size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            break
        yield kind, data[offset + header : offset + size]
        offset += size


def _child(data: memoryview, kind: bytes) -> memoryview:
    return next((body for name, body in _boxes(data) if name == kind), memoryview(b""))


def _duration(mdhd: memoryview) -> float:
    if not mdhd or mdhd[0] not in (0, 1):
        return 0
    offset, width = (20, 8) if mdhd[0] == 1 else (12, 4)
    if len(mdhd) < offset + 4 + width:
        return 0
    scale = int.from_bytes(mdhd[offset : offset + 4], "big")
    ticks = int.from_bytes(mdhd[offset + 4 : offset + 4 + width], "big")
    return ticks / scale if scale else 0


def _dimensions(tkhd: memoryview, stsd: memoryview) -> tuple[int, int]:
    if len(stsd) >= 44:
        width = int.from_bytes(stsd[40:42], "big")
        height = int.from_bytes(stsd[42:44], "big")
        if width and height:
            return width, height
    return int.from_bytes(tkhd[-8:-4], "big") >> 16, int.from_bytes(tkhd[-4:], "big") >> 16


def _maximum_fps(table: memoryview, mdhd: memoryview) -> float:
    if not mdhd or mdhd[0] not in (0, 1):
        return 0
    offset = 20 if mdhd[0] == 1 else 12
    scale = int.from_bytes(mdhd[offset : offset + 4], "big")
    timing = _child(table, b"stts")
    count = int.from_bytes(timing[4:8], "big")
    if not 0 < count <= 100_000 or len(timing) < 8 + count * 8:
        return 0
    deltas = (
        int.from_bytes(timing[index + 4 : index + 8], "big")
        for index in range(8, 8 + count * 8, 8)
        if int.from_bytes(timing[index : index + 4], "big")
    )
    minimum = min((delta for delta in deltas if delta > 0), default=0)
    fps = scale / minimum if minimum else 0
    return fps if 0 < fps <= 1000 else 0


def _track(track: memoryview, *, peak_fps: bool = False) -> tuple[FormatInfo, float] | None:
    tkhd, mdia = _child(track, b"tkhd"), _child(track, b"mdia")
    handler = _child(mdia, b"hdlr")
    if len(tkhd) < 84 or handler[8:12] != b"vide":
        return None
    table = _child(_child(mdia, b"minf"), b"stbl")
    stsd = _child(table, b"stsd")
    width, height = _dimensions(tkhd, stsd)
    if not (0 < width <= 16384 and 0 < height <= 16384):
        return None
    codec = bytes(stsd[12:16])
    if codec not in {b"avc1", b"avc3", b"hvc1", b"hev1", b"av01", b"vp09"}:
        return None
    mdhd = _child(mdia, b"mdhd")
    duration = _duration(mdhd)
    samples = int.from_bytes(_child(table, b"stsz")[8:12], "big")
    fps = samples / duration if duration else 0
    if peak_fps:
        fps = max(fps, _maximum_fps(table, mdhd))
    fmt = FormatInfo(
        width=width,
        height=height,
        fps=fps if 0 < fps <= 1000 else 0,
        codec={b"avc1": "H.264", b"avc3": "H.264", b"hvc1": "H.265", b"hev1": "H.265"}.get(
            codec, codec.decode("ascii")
        ),
    )
    return fmt, duration


def _movies(data: bytes, tail: bool) -> Iterator[memoryview]:
    view = memoryview(data)
    yield from (body for name, body in _boxes(view) if name == b"moov")
    if not tail:
        return
    offset = 0
    # 文件末尾可能从 mdat 中间开始；只在有界切片里寻找完整 moov，不递归扫描媒体数据。
    for _ in range(32):
        found = data.find(b"moov", offset)
        if found < 0:
            break
        offset = found + 4
        if found < 4:
            continue
        for name, body in _boxes(view[found - 4 :]):
            if name == b"moov":
                yield body
            break


def metadata(data: bytes, *, tail: bool = False, peak_fps: bool = False) -> tuple[FormatInfo, float] | None:
    tracks = (
        info
        for movie in _movies(data, tail)
        for name, body in _boxes(movie)
        if name == b"trak" and (info := _track(body, peak_fps=peak_fps)) is not None
    )
    return max(tracks, key=lambda item: (item[1], short_side(item[0].width, item[0].height)), default=None)


def local_metadata(path: Path, *, peak_fps: bool = False) -> tuple[FormatInfo, float] | None:
    """只读有界 moov；默认平均帧率，peak_fps 按帧时序表核对 HLS 的峰值声明。"""
    total = path.stat().st_size
    with path.open("rb") as source:
        for _ in range(128):
            start = source.tell()
            header = source.read(8)
            if len(header) != 8:
                break
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            if size == 1:
                extended = source.read(8)
                if len(extended) != 8:
                    break
                size = int.from_bytes(extended, "big")
            size = size or total - start
            if size < source.tell() - start or start + size > total:
                break
            if kind == b"moov":
                if size > 16 << 20:
                    break
                source.seek(start)
                return metadata(source.read(size), peak_fps=peak_fps)
            source.seek(start + size)
    return None


def _response_size(response: httpx.Response, start: int) -> int:
    if response.status_code == 206:
        match = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
        if not match or int(match[1]) != start or not start <= int(match[2]) < int(match[3]):
            raise ValueError("媒体探测未返回请求的字节范围")
        return int(match[3])
    if start:
        raise ValueError("媒体源不支持尾部范围读取")
    return int(response.headers.get("content-length") or 0)


async def _slice(url: str, start: int, limit: int, headers: dict[str, str]) -> tuple[bytes, int]:
    headers = headers | {"Range": f"bytes={start}-{start + limit - 1}", "Accept-Encoding": "identity"}
    async with (
        safe_client(for_url=url, follow_redirects=True, timeout=5) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("媒体探测不接受压缩响应")
        total = _response_size(response, start)
        if response.is_stream_consumed:
            return response.content[:limit], total
        data = bytearray()
        async for chunk in response.aiter_raw(32 * 1024):
            data.extend(chunk[: limit - len(data)])
            if len(data) >= limit:
                break
    return bytes(data), total


async def _probe(url: str, headers: dict[str, str], request_url: str = "") -> FormatInfo | None:
    target = request_url or url
    data, total = await _slice(target, 0, _HEAD_BYTES, headers)
    info = metadata(data)
    if info is None and total > len(data):
        tail, _ = await _slice(target, max(0, total - _TAIL_BYTES), _TAIL_BYTES, headers)
        info = metadata(tail, tail=True)
    if info is None:
        return None
    fmt, duration = info
    fmt.url, fmt.filesize = url, total
    fmt.bitrate = int(total * 8 / duration) if duration and total else 0
    fmt.label = f"{short_side(fmt.width, fmt.height)}p" + (str(round(fmt.fps)) if fmt.fps > 45 else "")
    return fmt


async def _attempts(url: str, headers: dict[str, str]) -> FormatInfo | None:
    try:
        return await _probe(url, headers)
    except httpx.HTTPError:
        edge = relay.edge_media_url(url, 300) if relay.edge_media_enabled() else None
        if not edge:
            raise
        # /relay 会缓冲完整响应；媒体探测只使用可中途关闭的流式 /media 备用入口。
        return await _probe(url, headers, edge)


async def probe(url: str, headers: dict[str, str] | None = None) -> FormatInfo | None:
    """每个入口最多读取 1.5 MiB、总耗时 8 秒；不虚构元数据，也不阻止既有解析路径。"""
    try:
        return await asyncio.wait_for(_attempts(url, headers or {}), 8)
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
        return None
