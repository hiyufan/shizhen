"""从 Discord 官方匿名目录匹配公开贴纸；不调用需凭证的单包或服务器接口。"""

import re
from urllib.parse import urlparse

import httpx

from .base import BaseParser, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError

CATALOG = "https://discord.com/api/v10/sticker-packs"
CDN = "https://cdn.discordapp.com/stickers/"
EXAMPLE_PACKS = [
    ("Wumpus Beyond", "https://discord.com/store/skus/847199849233514547"),
    ("Mallow The Rascal", "https://discord.com/store/skus/796138864933863454"),
]
_ID = re.compile(r"[0-9]{15,20}\Z")
_HOSTS = {"discord.com", "www.discord.com", "ptb.discord.com", "canary.discord.com"}
_CDN_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
_FORMATS = {1: ("png", "image"), 2: ("png", "animation"), 3: ("json", "animation"), 4: ("gif", "animation")}


def is_sticker_page(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.hostname in _CDN_HOSTS:
        return parsed.path.startswith("/stickers/")
    return parsed.hostname in _HOSTS and parsed.path.startswith(
        ("/stickers/", "/sticker-packs/", "/store/skus/", "/api/v10/sticker-packs/")
    )


def reference(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not is_sticker_page(url):
        raise ParseError("unsupported", "请粘贴 Discord 公开贴纸包或贴纸素材链接")
    patterns = {
        "sticker": r"/stickers/([0-9]{15,20})(?:\.(?:png|json|gif))?/?",
        "pack": r"/(?:api/v10/)?sticker-packs/([0-9]{15,20})/?",
        "sku": r"/store/skus/([0-9]{15,20})(?:/[^/]+)?/?",
    }
    for kind, pattern in patterns.items():
        if match := re.fullmatch(pattern, parsed.path):
            return kind, match[1]
    raise ParseError("unsupported", "Discord 贴纸链接无效，请复制完整链接")


def _asset(row: dict) -> MediaAsset | None:
    ident = row.get("id")
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise ParseError("parse", "Discord 返回的贴纸编号无效")
    format_type = row.get("format_type")
    if row.get("available") is False or not isinstance(format_type, int) or format_type not in _FORMATS:
        return None
    extension, kind = _FORMATS[format_type]
    url = CDN + ident + "." + extension
    return MediaAsset(
        url=url,
        label=str(row.get("name") or ident),
        kind=kind,
        cover_url="" if extension == "json" else url,
        ext=extension,
    )


def _select(packs: list, kind: str, ident: str) -> tuple[dict, list]:
    for pack in packs:
        rows = pack.get("stickers") or []
        if not isinstance(rows, list) or len(rows) > 200:
            raise ParseError("parse", "Discord 贴纸目录内容异常")
        if kind in {"pack", "sku"} and pack.get("sku_id" if kind == "sku" else "id") == ident:
            return pack, rows
        if kind == "sticker":
            for row in rows:
                if isinstance(row, dict) and row.get("id") == ident:
                    return pack, [row]
    raise ParseError("restricted", "这份贴纸不在 Discord 当前公开目录中，暂时无法匿名读取")


class Discord(BaseParser):
    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": "DiscordBot (https://github.com/hiyufan/shizhen, 0.1)", "Accept": "application/json"}

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self._parse(*reference(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not _ID.fullmatch(video_id):
            raise ParseError("unsupported", "Discord 贴纸编号无效")
        return await self._parse("sticker", video_id)

    async def _parse(self, kind: str, ident: str) -> VideoInfo:
        try:
            response = await self.get_json(CATALOG, timeout=15)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise ParseError("blocked", "Discord 公开目录请求过于频繁，请稍后再试") from exc
            raise ParseError("restricted", "Discord 当前没有提供可用的公开贴纸目录") from exc
        packs = response.get("sticker_packs") if isinstance(response, dict) else None
        if not isinstance(packs, list) or len(packs) > 50 or any(not isinstance(pack, dict) for pack in packs):
            raise ParseError("parse", "Discord 贴纸目录内容异常")
        pack, rows = _select(packs, kind, ident)
        assets = [asset for row in rows if isinstance(row, dict) and (asset := _asset(row))]
        if not assets:
            raise ParseError("empty", "这个 Discord 贴纸包没有可用的公开文件")
        return VideoInfo(
            video_url="",
            cover_url=next((asset.cover_url for asset in assets if asset.cover_url), ""),
            title=assets[0].label if kind == "sticker" else str(pack.get("name") or ident),
            assets=assets,
            author=VideoAuthor(name="Discord"),
            page_url=CDN + ident + "." + assets[0].ext
            if kind == "sticker"
            else "https://discord.com/store/skus/" + str(pack.get("sku_id") or ident),
            notice="素材来自 Discord 官方公开贴纸目录，不包含私人服务器内容；Lottie 动画可转换后预览。",
        )
