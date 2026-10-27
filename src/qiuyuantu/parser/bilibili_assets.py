"""B 站装扮与收藏集的公开展示素材，不读取购买凭据或客户端缓存。"""

import re
from urllib.parse import parse_qs, urlencode, urlparse

from .base import BaseParser, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError

SUIT_PATH = "/h5/mall/suit/detail"
COLLECTION_PATH = "/h5/mall/digital-card/home"
SUIT_API = "https://api.bilibili.com/x/garb/v2/mall/suit/detail"
COLLECTION_API = "https://api.bilibili.com/x/vas/dlc_act/lottery_home_detail"
REFERER = "https://www.bilibili.com/"
PROPERTY_LABELS = {
    "head_myself_mp4_bg": "动态背景",
    "head_myself_bg": "个人页背景",
    "head_myself_squared_bg": "方形背景",
    "head_bg": "顶部背景",
    "head_tab_bg": "标签背景",
    "image": "图片",
    "image_cover": "封面",
    "image_preview": "预览图",
    "image_enhance": "大图",
    "image_ani": "动态图片",
    "image_ani_cut": "裁切动态图片",
    "image_animate": "动态图片",
    "image_gif": "动态图片",
}


def is_asset_page(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in {"www.bilibili.com", "m.bilibili.com"} and parsed.path.rstrip("/") in {
        SUIT_PATH,
        COLLECTION_PATH,
    }


def asset_ref(url: str) -> tuple[str, dict[str, str]]:
    if not is_asset_page(url) or urlparse(url).scheme not in {"http", "https"}:
        raise ParseError("unsupported", "请复制 B 站装扮或收藏集详情页的完整链接")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    keys = ("id",) if parsed.path.rstrip("/") == SUIT_PATH else ("act_id", "lottery_id")
    values = {key: query.get(key, [""])[0] for key in keys}
    if not all(re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in values.values()):
        raise ParseError("unsupported", "请保留装扮编号，或收藏集链接中的 act_id 和 lottery_id")
    return parsed.path.rstrip("/"), values


def _url(value: str) -> str:
    if not value:
        return ""
    value = "https:" + value if value.startswith("//") else value
    parsed = urlparse(value)
    host = parsed.hostname or ""
    allowed = any(host == domain or host.endswith("." + domain) for domain in ("hdslb.com", "bilivideo.com"))
    allowed = allowed or host == "upos-hz-mirrorakam.akamaized.net"
    if parsed.scheme not in {"http", "https"} or not allowed or parsed.username or parsed.port:
        raise ParseError("parse", "B 站返回的素材地址不可用")
    return value


def _property_assets(item: dict) -> list[MediaAsset]:
    assets = []
    for key, label in PROPERTY_LABELS.items():
        url = _url((item.get("properties") or {}).get(key) or "")
        if not url:
            continue
        suffix = urlparse(url).path.lower().rsplit(".", 1)[-1]
        if suffix not in {"jpg", "jpeg", "png", "webp", "gif", "mp4", "webm", "svga", "pag", "bin"}:
            continue
        if suffix == "bin" and key not in {"image_ani", "image_ani_cut", "image_animate"}:
            continue
        kind = "video" if suffix in {"mp4", "webm"} else "image"
        if suffix == "gif" or key in {"image_ani", "image_animate", "image_gif"}:
            kind = "animation"
        special = suffix in {"bin", "svga", "pag"}
        cover = _url((item.get("properties") or {}).get("image_preview") or "") if special else ""
        assets.append(
            MediaAsset(
                url=url,
                label=(item.get("name") or "") + " · " + label,
                kind="animation" if special else kind,
                cover_url=cover,
                ext=("svga" if suffix == "bin" else suffix) if special else "",
            )
        )
    return assets


def _suit_assets(data: dict) -> list[MediaAsset]:
    item = data.get("item") or data
    assets = _property_assets(item)
    for items in (data.get("suit_items") or {}).values():
        for row in items or []:
            assets.extend(_property_assets(row))
            for child in row.get("items") or []:
                assets.extend(_property_assets(child))
    return list({asset.url: asset for asset in assets}.values())


def _card_assets(data: dict) -> list[MediaAsset]:
    assets = []
    for row in data.get("item_list") or []:
        card = row.get("card_info") or {}
        cover = _url(card.get("card_img") or "")
        name = card.get("card_name") or ""
        if cover:
            assets.append(MediaAsset(url=cover, label=name, kind="image"))
        # video_list 是同一段视频的 CDN 备份，不是不同素材或清晰度。
        videos = card.get("video_list") or []
        if videos:
            assets.append(MediaAsset(url=_url(videos[0]), label=name, kind="video", cover_url=cover))
    return assets


class BiliAssets(BaseParser):
    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua("windows"), "Referer": REFERER}

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        path, params = asset_ref(share_url)
        if path == SUIT_PATH:
            return await self.parse_video_id(params["id"])
        response = await self.get_json(COLLECTION_API, params=params)
        data = self._data(response)
        if str(data.get("lottery_id")) != params["lottery_id"]:
            raise ParseError("parse", "B 站返回的收藏集编号不匹配")
        return self._info(data.get("name") or "B站收藏集", _card_assets(data), path, params)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[1-9][0-9]{0,19}", video_id):
            raise ParseError("unsupported", "B 站装扮编号无效")
        response = await self.get_json(SUIT_API, params={"item_id": video_id, "part": "suit"})
        data = self._data(response)
        item = data.get("item") or data
        if not item.get("item_id"):
            raise ParseError("empty", "B 站没有返回这套装扮的公开素材")
        if str(item["item_id"]) != video_id:
            raise ParseError("parse", "B 站返回的装扮编号不匹配")
        return self._info(item.get("name") or "B站装扮", _suit_assets(data), SUIT_PATH, {"id": video_id})

    @staticmethod
    def _data(response: dict) -> dict:
        if response.get("code") != 0:
            raise ParseError("restricted", "B 站暂未提供这套素材的公开数据")
        return response.get("data") or {}

    @staticmethod
    def _info(title: str, assets: list[MediaAsset], path: str, params: dict) -> VideoInfo:
        if not assets:
            raise ParseError("empty", "这套素材暂时没有可下载的公开图片或视频")
        return VideoInfo(
            video_url="",
            cover_url=next((asset.url for asset in assets if asset.kind == "image"), ""),
            title=title,
            assets=assets,
            author=VideoAuthor(name="bilibili"),
            page_url=REFERER.rstrip("/") + path + "?" + urlencode(params),
            video_headers={"Referer": REFERER},
            notice="仅展示平台公开返回的素材；下载文件不会解锁账号装扮或收藏集。",
        )
