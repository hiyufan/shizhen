"""Pinterest 公开单 Pin 的图片、轮播和视频；不抓取推荐流或账号收藏。"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

import httpx

from ..utils import gather_or_cancel
from .base import BaseParser, FormatInfo, ImgInfo, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, direct_format, number

DOMAINS = (
    "pinterest.com",
    "pinterest.co.uk",
    "pinterest.ca",
    "pinterest.de",
    "pinterest.fr",
    "pinterest.jp",
    "pinterest.com.au",
    "pinterest.es",
    "pinterest.it",
    "pinterest.co.kr",
    "pinterest.pt",
    "pinterest.ch",
    "pinterest.at",
    "pinterest.nz",
    "pinterest.com.mx",
    "pin.it",
)
ROOT = "https://www.pinterest.com"
MAX_MEDIA = 100


def _media_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    host = parsed.hostname or ""
    return (
        value
        if parsed.scheme == "https" and not parsed.username and (host == "pinimg.com" or host.endswith(".pinimg.com"))
        else ""
    )


def _image(images: dict) -> str:
    for key in ("orig", "originals", "original"):
        if url := _media_url((images.get(key) or {}).get("url")):
            return url
    rows = [row for row in images.values() if isinstance(row, dict) and _media_url(row.get("url"))]
    best = max(rows, key=lambda row: number(row.get("width")) * number(row.get("height")), default={})
    return _media_url(best.get("url"))


def _video(video: dict) -> dict:
    rows = [
        row for row in (video.get("video_list") or {}).values() if isinstance(row, dict) and _media_url(row.get("url"))
    ]
    # MP4 可直接下载和转换；只有 HLS 时交给受控服务端下载，不冒充 MP4 文件。
    files = [row for row in rows if urlparse(row["url"]).path.lower().endswith(".mp4")]
    return max(files or rows, key=lambda row: (number(row.get("height")), number(row.get("width"))), default={})


def _block_media(block: dict, page: dict) -> MediaAsset | None:
    if video := _video(block.get("video") or block.get("video_data_v2") or {}):
        cover = _media_url(video.get("thumbnail")) or _image(page.get("images") or {})
        return MediaAsset(video["url"], kind="video", cover_url=cover)
    image = block.get("image") or {}
    url = _image(image.get("images") or {}) or _image(block.get("images") or {})
    return (
        MediaAsset(url, kind="animation" if urlparse(url).path.lower().endswith(".gif") else "image") if url else None
    )


def _assets(pin: dict) -> list[MediaAsset]:
    story = pin.get("story_pin_data") or {}
    pages = story.get("pages") or []
    slots = (pin.get("carousel_data") or {}).get("carousel_slots") or []
    if len(pages) > MAX_MEDIA or len(slots) > MAX_MEDIA:
        raise ParseError("parse", "Pinterest 返回的媒体数量超出限制")
    result = []
    for page in pages or slots:
        blocks = page.get("blocks") or [page]
        if len(blocks) > MAX_MEDIA:
            raise ParseError("parse", "Pinterest 返回的媒体数量超出限制")
        for block in blocks:
            if item := _block_media(block, page):
                result.append(item)
            if len(result) > MAX_MEDIA:
                raise ParseError("parse", "Pinterest 返回的媒体数量超出限制")
    return result


def _from_pin(pin: dict, ident: str) -> VideoInfo:
    user = pin.get("pinner") or pin.get("native_creator") or {}
    cover = _image(pin.get("images") or {})
    info = VideoInfo(
        "",
        cover,
        title=pin.get("title") or pin.get("grid_title") or pin.get("description") or "Pinterest",
        page_url=f"{ROOT}/pin/{ident}/",
        author=VideoAuthor(name=user.get("full_name") or user.get("username") or ""),
    )
    assets = _assets(pin)
    if assets:
        # 用同一数组保持混合轮播的原顺序，不能拆成图片和视频后重新分组。
        for index, item in enumerate(assets, 1):
            item.label = f"Pinterest · {index}"
        info.assets = assets
    elif video := _video(pin.get("videos") or {}):
        url = video["url"]
        info.duration = number(video.get("duration")) / 1000
        if urlparse(url).path.lower().endswith(".m3u8"):
            info.page_url = url
            info.formats = [FormatInfo(label="MP4", format_spec="bv*+ba/b/bv", height=int(number(video.get("height"))))]
        else:
            apply_video_formats(info, [direct_format(url, video)])
    elif cover and urlparse(cover).path.lower().endswith(".gif"):
        info.assets = [MediaAsset(cover, label="Pinterest", kind="animation")]
    elif cover:
        info.images = [ImgInfo(cover)]
    else:
        raise ParseError("empty", "Pinterest 没有提供这条 Pin 的公开媒体")
    return info


def _find_pin(data: object, ident: str) -> dict | None:
    pending = [data]
    for _ in range(20000):
        if not pending:
            return None
        item = pending.pop()
        if isinstance(item, dict):
            if str(item.get("id")) == ident and any(
                key in item for key in ("images", "videos", "story_pin_data", "carousel_data")
            ):
                return item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return None


def _normalize(value: object, depth: int = 0) -> object:
    """公开页面的 Relay 数据使用 camelCase，媒体字段与旧接口保持同一处理路径。"""
    if depth > 40:
        raise ValueError("Pinterest 数据嵌套过深")
    if isinstance(value, list):
        return [_normalize(item, depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    images = {}
    for key, item in value.items():
        name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower()
        normalized = _normalize(item, depth + 1)
        result[name] = normalized
        if name.startswith("images_") and isinstance(normalized, dict):
            images[name.removeprefix("images_")] = normalized
    if images:
        result["images"] = images
    if "entity_id" in result:
        result["id"] = str(result["entity_id"])
    return result


def _page_pin(page: str, ident: str) -> dict | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"<script\b[^>]*\btype=[\"\']application/json[\"\'][^>]*>(.*?)</script>", page, re.S):
        try:
            pin = _find_pin(_normalize(json.loads(match[1])), ident)
        except ValueError:
            continue
        if pin:
            return pin
    # 只读取回调参数中的 JSON，不执行平台脚本。每份结果仍须匹配当前 Pin ID。
    for match in re.finditer(r"window\.__PWS_RELAY_REGISTER_COMPLETED_REQUEST__\(\s*", page):
        try:
            _, end = decoder.raw_decode(page, match.end())
            separator = re.match(r"\s*,\s*", page[end:])
            if not separator:
                continue
            data, _ = decoder.raw_decode(page, end + separator.end())
            pin = _find_pin(_normalize(data), ident)
        except ValueError:
            continue
        if pin:
            return pin
    return None


class Pinterest(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        parsed = urlparse(share_url)
        if parsed.hostname == "pin.it":
            if not re.fullmatch(r"/[A-Za-z0-9_-]{1,100}/?", parsed.path):
                raise ParseError("unsupported", "请使用 Pinterest 单条 Pin 的分享链接")
            target = await self.redirect_target(
                f"https://api.pinterest.com/url_shortener/{parsed.path.strip('/')}/redirect/"
            )
            share_url = urljoin(ROOT, target)
            parsed = urlparse(share_url)
        host = parsed.hostname or ""
        match = re.fullmatch(r"/pin/(\d{1,30})(?:/sent)?/?", parsed.path)
        valid_host = any(host == domain or host.endswith("." + domain) for domain in DOMAINS if domain != "pin.it")
        if not valid_host or not match:
            raise ParseError("unsupported", "请使用 Pinterest 单条 Pin 的分享链接，暂不支持画板与搜索页")
        return await self.parse_video_id(match[1])

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"\d{1,30}", video_id):
            raise ParseError("unsupported", "Pinterest Pin ID 无效")
        headers = {"User-Agent": self.ua("windows"), "Referer": ROOT + "/", "X-Requested-With": "XMLHttpRequest"}
        try:
            body = await self.get_json(
                ROOT + "/resource/PinResource/get/",
                headers=headers,
                params={
                    "source_url": f"/pin/{video_id}/",
                    "data": json.dumps({"options": {"id": video_id, "field_set_key": "detailed"}}),
                },
            )
            pin = (body.get("resource_response") or {}).get("data")
            if isinstance(pin, dict) and str(pin.get("id")) == video_id:
                return await self._originals(_from_pin(pin, video_id))
        except (httpx.HTTPError, ValueError):
            pass
        page = await self.get_text(f"{ROOT}/pin/{video_id}/", headers=headers, follow_redirects=True)
        if pin := _page_pin(page, video_id):
            return await self._originals(_from_pin(pin, video_id))
        raise ParseError("restricted", "Pinterest 没有提供这条 Pin 的公开数据，可能已删除或需要登录")

    async def _originals(self, info: VideoInfo) -> VideoInfo:
        urls = {image.url for image in info.images} | {asset.url for asset in info.assets if asset.kind != "video"}
        urls.add(info.cover_url)
        candidates = {url for url in urls if re.search(r"/\d+x(?:\d+)?/", url)}
        gate = asyncio.Semaphore(4)

        async def probe(url):
            async with gate:
                candidate = re.sub(r"/\d+x(?:\d+)?/", "/originals/", url, count=1)
                try:
                    response = await self.fetch("HEAD", candidate, follow_redirects=True)
                    if response.headers.get("content-type", "").startswith("image/") and _media_url(str(response.url)):
                        return str(response.url)
                except httpx.HTTPError:
                    pass
                return url

        ordered = sorted(candidates)
        upgraded = dict(zip(ordered, await gather_or_cancel(*(probe(url) for url in ordered)), strict=True))
        info.cover_url = upgraded.get(info.cover_url, info.cover_url)
        for image in info.images:
            image.url = upgraded.get(image.url, image.url)
        for asset in info.assets:
            asset.url = upgraded.get(asset.url, asset.url)
        return info
