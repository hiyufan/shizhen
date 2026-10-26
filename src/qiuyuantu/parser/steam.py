"""Steam 点数商店公开素材；按物品 ID 查询，不读取账号或购买状态。"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

CDN = "https://shared.fastly.steamstatic.com/community_assets/images/items"


def reward_id(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or parsed.hostname != "store.steampowered.com":
        raise ParseError("unsupported", "请使用 Steam 点数商店的单个物品链接")
    match = re.fullmatch(r"/points/shop/(?:(?:app/([0-9]{1,12})|c/[a-z_-]+)/)?reward/([0-9]{1,12})/?", parsed.path)
    if not match:
        raise ParseError("unsupported", "请打开 Steam 点数商店里的单个物品，再复制地址栏链接")
    return match[2], match[1] or ""


def asset_url(appid: int, name: str) -> str:
    if not name:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./-]{1,300}", name) or any(part in {".", "..", ""} for part in name.split("/")):
        raise ParseError("parse", "Steam 返回的素材地址不可用")
    return f"{CDN}/{appid}/{name}"


class Steam(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        ident, app = reward_id(share_url)
        data = await self.get_json(
            "https://api.steampowered.com/ILoyaltyRewardsService/QueryRewardItems/v1/",
            params={"input_json": '{"definitionids":[' + ident + '],"count":1,"language":"schinese"}'},
        )
        rows = (data.get("response") or {}).get("definitions") or []
        item = next((row for row in rows if str(row.get("defid")) == ident), None)
        if not item or (app and str(item.get("appid")) != app):
            raise ParseError("deleted", "Steam 没有返回这个物品的公开素材")
        appid = int(item["appid"])
        media = item.get("community_item_data") or {}
        # large 是静态预览；头像 GIF、头像框与贴纸 APNG 在 small，不能按文件名 PNG 判断静态。
        image_key = (
            "item_image_small"
            if media.get("animated") or item.get("community_item_class") == 15
            else "item_image_large"
        )
        image = asset_url(appid, media.get(image_key) or media.get("item_image_small") or "")
        video = asset_url(appid, media.get("item_movie_mp4") or media.get("item_movie_webm") or "")
        if not image and not video:
            raise ParseError("empty", "Steam 没有提供可下载的图片或视频")
        return VideoInfo(
            video_url=video,
            cover_url=image,
            title=media.get("item_title") or media.get("item_name") or "Steam",
            author=VideoAuthor(name="Steam"),
            images=[ImgInfo(url=image)] if image else [],
            formats=[FormatInfo(label="WebM", url=asset_url(appid, media["item_movie_webm"]), ext="webm")]
            if media.get("item_movie_mp4") and media.get("item_movie_webm")
            else [],
            page_url=f"https://store.steampowered.com/points/shop/app/{appid}/reward/{ident}",
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[0-9]{1,12}", video_id):
            raise ParseError("unsupported", "Steam 物品 ID 无效")
        return await self.parse_share_url(f"https://store.steampowered.com/points/shop/reward/{video_id}")
