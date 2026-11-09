"""Steam 商店、点数商店与社区的公开素材；按物品 / 作品 ID 查询，不读取账号或购买状态。"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .steam_community import SteamCommunity
from .steam_links import classify_link, reward_id, shared_file_id
from .steam_store import REWARDS_API, SteamStore

CDN = "https://shared.fastly.steamstatic.com/community_assets/images/items"

__all__ = ["CDN", "Steam", "asset_url", "reward_id", "shared_file_id"]


def asset_url(appid: int, name: str) -> str:
    if not name:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./-]{1,300}", name) or any(part in {".", "..", ""} for part in name.split("/")):
        raise ParseError("parse", "Steam 返回的素材地址不可用")
    return f"{CDN}/{appid}/{name}"


class Steam(SteamStore, SteamCommunity, BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        link = classify_link(share_url)
        if link.kind == "short":
            # s.team/a/<appid> 等官方短链跳到商店或社区，归类后照常解析
            target = urljoin(share_url, await self.redirect_target(share_url))
            link = classify_link(target)
            if link.kind == "short":
                raise ParseError("unsupported", "Steam 短链没有跳到可解析的页面")
            share_url = target
        handlers = {
            "reward": lambda: self._reward(link.ident, link.app),
            "points_app": lambda: self.points_app(link.ident),
            "app": lambda: self.store_app(link.ident),
            "shared": lambda: self.shared_file(link.ident),
            "profile": lambda: self.profile(link.ident),
        }
        handler = handlers.get(link.kind)
        return await handler() if handler else await self.community_video(share_url)

    @staticmethod
    def reward_media(item: dict) -> tuple[str, str]:
        """点数商店物品的 (图片, 视频) 地址。"""
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
        return image, video

    async def _reward(self, ident: str, app: str) -> VideoInfo:
        query = {"definitionids": [int(ident)], "count": 1, "language": "schinese"}
        data = await self.get_json(REWARDS_API, params={"input_json": json.dumps(query, separators=(",", ":"))})
        rows = (data.get("response") or {}).get("definitions") or []
        item = next((row for row in rows if str(row.get("defid")) == ident), None)
        if not item or (app and str(item.get("appid")) != app):
            raise ParseError("deleted", "Steam 没有返回这个物品的公开素材")
        appid = int(item["appid"])
        media = item.get("community_item_data") or {}
        image, video = self.reward_media(item)
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
