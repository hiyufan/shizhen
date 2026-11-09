"""Steam 商店游戏页与点数商店游戏物品页；只读公开商店接口，不带账号与地区设置。"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlparse

from . import manifests
from .base import ContentEntry, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import direct_format

STORE_ROOT = "https://store.steampowered.com"
REWARDS_API = "https://api.steampowered.com/ILoyaltyRewardsService/QueryRewardItems/v1/"
# 商店截图 ss_<SHA1>.<宽>x<高>.jpg 是缩放版，去掉尺寸后缀才是开发者上传的原图（常见 4K）
SCREENSHOT = re.compile(
    r"https://[a-z0-9.-]+\.steamstatic\.com/(?:store_item_assets/)?steam/apps/[0-9]{1,12}/"
    r"(?:[0-9a-f]{40}/)?ss_[0-9a-f]{40}(?:\.[0-9]{2,5}x[0-9]{2,5})?\.jpg"
)
TRAILER = re.compile(r"https://video\.[a-z]+\.steamstatic\.com/store_trailers/[0-9A-Za-z_./-]+\.m3u8")
MAX_SCREENSHOTS = 50
MAX_REWARDS = 100


def original_screenshot(url: object) -> str:
    base = str(url or "").split("?")[0]
    return re.sub(r"\.[0-9]+x[0-9]+(?=\.jpg$)", "", base) if SCREENSHOT.fullmatch(base) else ""


def _steamstatic(url: object) -> str:
    base = str(url or "").split("?")[0]
    host = urlparse(base).hostname or ""
    return base if base.startswith("https://") and host.endswith(".steamstatic.com") else ""


def _main_trailer(movies: list) -> str:
    rows = [row for row in movies if isinstance(row, dict)]
    row = next((row for row in rows if row.get("highlight")), rows[0] if rows else {})
    url = str(row.get("hls_h264") or "")
    return url if TRAILER.fullmatch(url.split("?")[0]) else ""


class SteamStore:
    async def store_app(self, appid: str) -> VideoInfo:
        body = await self.get_json(STORE_ROOT + "/api/appdetails", params={"appids": appid, "l": "schinese"})
        entry = body.get(appid) if isinstance(body, dict) else None
        if not isinstance(entry, dict) or not entry.get("success") or not isinstance(entry.get("data"), dict):
            raise ParseError("deleted", "Steam 上找不到这个游戏")
        data = entry["data"]
        images = [
            ImgInfo(url) for row in data.get("screenshots") or [] if (url := original_screenshot(row.get("path_full")))
        ][:MAX_SCREENSHOTS]
        movies = data.get("movies") or []
        trailer = _main_trailer(movies)
        formats = await self._trailer_formats(trailer) if trailer else []
        if not images and not formats:
            raise ParseError("empty", "这个游戏的商店页没有截图或预告片")
        info = VideoInfo(
            "",
            _steamstatic(data.get("header_image")) or (images[0].url if images else ""),
            title=str(data.get("name") or appid),
            author=VideoAuthor(name=", ".join(str(name) for name in (data.get("developers") or [])[:3]) or "Steam"),
            images=images,
            formats=formats,
            # 预告片只有分段流，由服务端按清单合并；整页交给 yt-dlp 会把全部预告片都下载下来
            page_url=trailer or f"{STORE_ROOT}/app/{appid}/",
        )
        if formats and len(movies) > 1:
            info.notice = "商店页有多段预告片，这里提供主预告片。"
        return info

    async def _trailer_formats(self, url: str) -> list[FormatInfo]:
        text = await self.get_text(url)
        tracks = await asyncio.to_thread(manifests.tracks, text, url, "hls")
        formats = {}
        for track in tracks:
            fmt = direct_format(
                url, {"width": track.get("width"), "height": track.get("height"), "fps": track.get("fps")}
            )
            fmt.bitrate = int((track.get("tbr") or 0) * 1000)
            fmt.codec = str(track.get("vcodec") or "")
            if fmt.height:
                formats.setdefault(fmt.height, fmt)
        return sorted(formats.values(), key=lambda fmt: fmt.height, reverse=True)

    async def points_app(self, appid: str) -> VideoInfo:
        """某个游戏在点数商店里的全部物品；作为目录列出，逐个打开再下载。"""
        query = {"appids": [int(appid)], "count": MAX_REWARDS, "language": "schinese"}
        data = await self.get_json(REWARDS_API, params={"input_json": json.dumps(query, separators=(",", ":"))})
        response = data.get("response") or {}
        rows = [row for row in response.get("definitions") or [] if str(row.get("appid")) == appid]
        if not rows:
            raise ParseError("empty", "这个游戏在点数商店里没有物品")
        info = VideoInfo("", "", title="Steam 点数商店物品", author=VideoAuthor(name="Steam"), content_kind="directory")
        info.page_url = f"{STORE_ROOT}/points/shop/app/{appid}"
        info.entry_total = int(response.get("total_count") or len(rows))
        for row in rows:
            media = row.get("community_item_data") or {}
            info.entries.append(
                ContentEntry(
                    url=f"{STORE_ROOT}/points/shop/app/{appid}/reward/{int(row['defid'])}",
                    title=str(media.get("item_title") or media.get("item_name") or row["defid"]),
                    cover_url=self.reward_media(row)[0],
                    kind="reward",
                )
            )
        info.cover_url = next((entry.cover_url for entry in info.entries if entry.cover_url), "")
        return info
