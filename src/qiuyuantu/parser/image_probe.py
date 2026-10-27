"""只读取有界图片头验证尺寸，避免把缩略图或错误占位图当成更大的原图。"""

import asyncio
import re

import httpx
from PIL import Image, ImageFile

from ..convert.net import headers_for, safe_client

_LIMIT = 256 << 10


def _dimensions(parser: ImageFile.Parser) -> tuple[int, int] | None:
    if parser.image is None:
        return None
    width, height = parser.image.size
    if 0 < width <= 32768 and 0 < height <= 32768 and width * height <= 100_000_000:
        return width, height
    raise ValueError("图片尺寸超过探测限制")


async def _probe(url: str, headers: dict[str, str]) -> tuple[int, int] | None:
    parser = ImageFile.Parser()
    headers = headers_for(url, headers) | {"Range": f"bytes=0-{_LIMIT - 1}", "Accept-Encoding": "identity"}
    async with (
        safe_client(for_url=url, follow_redirects=True, timeout=5) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("图片探测不接受压缩响应")
        if response.status_code == 206:
            match = re.fullmatch(r"bytes 0-([0-9]+)/([0-9]+)", response.headers.get("content-range", ""))
            if not match or int(match[1]) >= int(match[2]):
                raise ValueError("图片探测返回了错误范围")
        if response.is_stream_consumed:
            parser.feed(response.content[:_LIMIT])
            return _dimensions(parser)
        count = 0
        async for chunk in response.aiter_raw(16 << 10):
            part = chunk[: _LIMIT - count]
            count += len(part)
            parser.feed(part)
            if size := _dimensions(parser):
                return size
            if count >= _LIMIT:
                break
    return None


async def probe(url: str, headers: dict[str, str] | None = None) -> tuple[int, int] | None:
    try:
        return await asyncio.wait_for(_probe(url, headers or {}), 8)
    except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError, asyncio.TimeoutError):
        return None
