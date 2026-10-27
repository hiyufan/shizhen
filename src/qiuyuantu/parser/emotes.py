"""公开直播表情：单个素材与有限集合，不请求 Twitch 账号接口。"""

import re
from urllib.parse import urlparse

import httpx

from .base import BaseParser, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError

_SEVEN_ID = r"(?:[0-9a-f]{24}|[0-9A-HJKMNP-TV-Z]{26})"
_HEX_ID = r"[0-9a-f]{24}"


def _reference(url: str, hosts: set[str], pattern: str) -> tuple[str, ...]:
    parsed = urlparse(url)
    match = re.fullmatch(pattern, parsed.path.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in hosts or not match:
        raise ParseError("unsupported", "请粘贴完整的公开表情或表情集合链接")
    return match.groups()


def _cdn(value: str, domains: set[str]) -> str:
    value = "https:" + value if value.startswith("//") else value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in domains or parsed.username or parsed.port:
        raise ParseError("parse", "平台返回的表情素材地址无效")
    return value


def _info(title: str, assets: list[MediaAsset], page: str, author: str) -> VideoInfo:
    if not assets or len(assets) > 200:
        raise ParseError("empty", "没有找到可用的公开表情，或集合超过 200 项")
    return VideoInfo(
        video_url="",
        cover_url=assets[0].cover_url,
        title=title,
        assets=assets,
        page_url=page,
        author=VideoAuthor(name=author),
        notice="素材来自平台公开目录；保留原格式，动态素材可继续转换。",
    )


def _seven_asset(row: dict) -> MediaAsset:
    data = row.get("data") or row
    if not re.fullmatch(_SEVEN_ID, str(data.get("id", ""))):
        raise ParseError("parse", "7TV 返回的表情编号无效")
    host = data.get("host") or {}
    # 静态 WebP 是有损转码；同尺寸的 PNG 保留更多像素信息。动态素材仍需保留完整帧序列。
    allowed = {"WEBP"} if data.get("animated") else {"PNG", "WEBP"}
    files = [f for f in host.get("files", []) if f.get("format") in allowed]
    if not files:
        raise ParseError("empty", "7TV 没有返回可用的 WebP 文件")
    chosen = max(files, key=lambda f: (int(f.get("width", 0)), int(f.get("height", 0)), f.get("format") == "PNG"))
    name = chosen.get("name", "")
    extension = chosen["format"].lower()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\." + extension, name):
        raise ParseError("parse", "7TV 返回的文件名无效")
    url = _cdn(str(host.get("url", "")) + "/" + name, {"cdn.7tv.app"})
    return MediaAsset(
        url=url,
        label=str(row.get("name") or data.get("name") or data["id"]),
        kind="animation" if data.get("animated") else "image",
        cover_url=url,
        ext=extension,
    )


class SevenTV(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        kind, ident = _reference(
            share_url, {"7tv.app", "7tv.io", "www.7tv.app"}, rf"/(emotes|emote-sets|sets)/({_SEVEN_ID}|global)"
        )
        if kind == "emotes":
            return await self.parse_video_id(ident)
        data = await self.get_json("https://7tv.io/v3/emote-sets/" + ident)
        rows = data.get("emotes")
        if not isinstance(rows, list) or len(rows) > 200:
            raise ParseError("restricted", "这个表情集合超过 200 项，请使用单个表情链接")
        return _info(str(data.get("name") or "7TV"), [_seven_asset(row) for row in rows], share_url, "7TV")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(_SEVEN_ID, video_id):
            raise ParseError("unsupported", "7TV 表情编号无效")
        data = await self.get_json("https://7tv.io/v3/emotes/" + video_id)
        if str(data.get("id")) != video_id:
            raise ParseError("parse", "7TV 返回的表情编号不匹配")
        asset = _seven_asset(data)
        return _info(asset.label, [asset], "https://7tv.app/emotes/" + video_id, "7TV")


def _bttv_asset(row: dict) -> MediaAsset:
    ident = str(row.get("id", ""))
    if not re.fullmatch(_HEX_ID, ident) or row.get("imageType") not in {"png", "gif", "webp"}:
        raise ParseError("parse", "BTTV 返回的表情信息无效")
    url = "https://cdn.betterttv.net/emote/" + ident + "/3x"
    return MediaAsset(
        url=url,
        label=str(row.get("code") or ident),
        ext=row["imageType"],
        kind="animation" if row.get("animated") or row["imageType"] == "gif" else "image",
        cover_url=url,
    )


class BetterTTV(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        (ident,) = _reference(share_url, {"betterttv.com", "www.betterttv.com"}, rf"/emotes/({_HEX_ID}|global)")
        if ident != "global":
            return await self.parse_video_id(ident)
        rows = await self.get_json("https://api.betterttv.net/3/cached/emotes/global")
        if not isinstance(rows, list) or len(rows) > 200:
            raise ParseError("parse", "BTTV 公开目录内容异常")
        return _info("BTTV", [_bttv_asset(row) for row in rows], share_url, "BetterTTV")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(_HEX_ID, video_id):
            raise ParseError("unsupported", "BTTV 表情编号无效")
        try:
            data = await self.get_json("https://api.betterttv.net/3/emotes/" + video_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {403, 404}:
                raise
            rows = await self.get_json("https://api.betterttv.net/3/cached/emotes/global")
            data = next((row for row in rows if row.get("id") == video_id), {})
            if not data:
                raise ParseError("restricted", "这份 BTTV 表情暂时无法匿名读取，请换一个公开链接") from exc
        if str(data.get("id")) != video_id:
            raise ParseError("parse", "BTTV 返回的表情编号不匹配")
        asset = _bttv_asset(data)
        return _info(asset.label, [asset], "https://betterttv.com/emotes/" + video_id, "BetterTTV")


def _ffz_asset(row: dict) -> MediaAsset:
    ident = str(row.get("id", ""))
    if not re.fullmatch(r"[1-9][0-9]{0,12}", ident):
        raise ParseError("parse", "FFZ 返回的表情编号无效")
    variants = row.get("animated") or row.get("urls") or {}
    if not isinstance(variants, dict) or not variants:
        raise ParseError("empty", "FFZ 没有返回图片文件")
    key = max(variants, key=lambda value: int(value))
    url = _cdn(variants[key], {"cdn.frankerfacez.com"})
    return MediaAsset(
        url=url,
        label=str(row.get("name") or ident),
        cover_url=url,
        kind="animation" if row.get("animated") else "image",
    )


class FrankerFaceZ(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        kind, ident = _reference(
            share_url,
            {"frankerfacez.com", "www.frankerfacez.com"},
            r"/(emoticon|set)/([1-9][0-9]{0,12}|global)(?:-[^/]+)?",
        )
        if kind == "emoticon":
            return await self.parse_video_id(ident)
        data = await self.get_json("https://api.frankerfacez.com/v1/set/" + ident)
        sets = data.get("sets") or {ident: data.get("set", {})}
        rows = [row for group in sets.values() for row in group.get("emoticons", [])]
        if len(rows) > 200:
            raise ParseError("restricted", "这个表情集合超过 200 项，请使用单个表情链接")
        return _info("FrankerFaceZ", [_ffz_asset(row) for row in rows], share_url, "FrankerFaceZ")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[1-9][0-9]{0,12}", video_id):
            raise ParseError("unsupported", "FFZ 表情编号无效")
        data = await self.get_json("https://api.frankerfacez.com/v1/emote/" + video_id)
        row = data.get("emote") or {}
        if str(row.get("id")) != video_id:
            raise ParseError("parse", "FFZ 返回的表情编号不匹配")
        asset = _ffz_asset(row)
        return _info(asset.label, [asset], "https://www.frankerfacez.com/emoticon/" + video_id, "FrankerFaceZ")
