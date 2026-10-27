"""匿名读取 Stickers.wiki 已公开收录的 Telegram 贴纸，不使用 Bot 或账号凭证。"""

import json
import re
from urllib.parse import urlparse

from lxml import html

from .base import BaseParser, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError

CATALOG = "https://stickers.wiki/telegram/"
CDN = "https://assets.stickers.wiki/img/"
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def is_sticker_page(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname in {"t.me", "telegram.me"} and parsed.path.startswith("/addstickers/")) or (
        parsed.hostname == "stickers.wiki" and parsed.path.startswith("/telegram/")
    )


def pack_name(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not is_sticker_page(url):
        raise ParseError("unsupported", "请粘贴 Telegram 的 addstickers 贴纸包链接")
    prefix = "/telegram/" if parsed.hostname == "stickers.wiki" else "/addstickers/"
    name = parsed.path.removeprefix(prefix).rstrip("/")
    if not _NAME.fullmatch(name):
        raise ParseError("unsupported", "贴纸包名称无效，请复制完整链接")
    return name


def _assets(rows: list) -> list[MediaAsset]:
    result = []
    for index, row in enumerate(rows):
        key, extension = row.get("storageKey", ""), row.get("format", "")
        if not re.fullmatch(r"[a-f0-9]{16,64}", key) or extension not in {"tgs", "webp"}:
            continue
        result.append(
            MediaAsset(
                url=CDN + key + "." + extension,
                cover_url=CDN + key + ".webp",
                label=row.get("emoji") or str(index + 1),
                kind="animation" if extension == "tgs" else "image",
                ext=extension,
            )
        )
    return result


class Telegram(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(pack_name(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not _NAME.fullmatch(video_id):
            raise ParseError("unsupported", "贴纸包名称无效，请复制完整链接")
        response = await self.fetch("GET", CATALOG + video_id.lower() + "/")
        if len(response.content) > 2_000_000:
            raise ParseError("too_large", "公开贴纸目录响应过大")
        tree = html.fromstring(response.content)
        packs = tree.xpath('//*[@id="sticker-dialog"]/@data-pack-data')
        if not packs:
            raise ParseError("restricted", "公开目录尚未收录这个贴纸包，无法匿名读取完整列表")
        data = json.loads(packs[0])
        if str(data.get("slug", "")).lower() != video_id.lower():
            raise ParseError("parse", "公开目录返回的贴纸包名称不匹配")
        rows = data.get("items") or []
        if len(rows) > 200:
            raise ParseError("too_large", "贴纸包超过 200 个素材，暂不支持整包解析")
        assets = _assets(rows)
        if not assets:
            raise ParseError("empty", "这个贴纸包没有可用的公开文件")
        title = tree.xpath("string(//h1)").strip() or data["slug"]
        return VideoInfo(
            video_url="",
            cover_url=assets[0].cover_url,
            title=title,
            assets=assets,
            author=VideoAuthor(name="Stickers.wiki"),
            page_url="https://t.me/addstickers/" + data["slug"],
            notice="素材来自 Stickers.wiki 公开收录，可能与 Telegram 当前版本不同。",
        )
