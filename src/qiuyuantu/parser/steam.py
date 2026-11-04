"""Steam 点数商店与社区作品的公开素材；按物品 / 作品 ID 查询，不读取账号或购买状态。"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

import httpx

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .ytdlp import YtDlp

CDN = "https://shared.fastly.steamstatic.com/community_assets/images/items"
COMMUNITY_HOSTS = ("steamcommunity.com", "www.steamcommunity.com")
# 艺术作品、截图和创意工坊预览图都放在 ugc/<内容 ID>/<SHA1>/；不带缩放参数就是上传时的原文件（GIF 保留动画）。
# 这个地址只响应 GET，HEAD 会返回 404。
UGC_IMAGE = re.compile(r"https://images\.steamusercontent\.com/ugc/[0-9]{1,20}/[0-9A-Fa-f]{40}/")
MAX_IMAGES = 50


def reward_id(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or parsed.hostname != "store.steampowered.com":
        raise ParseError("unsupported", "请使用 Steam 点数商店的单个物品链接")
    # 点数商店从个人点数页进入某个奖励时地址是 /points/profile/<steamid64>/reward/<defid>，
    # 其中的 steamid64 不是 appid，appid 仍由接口返回，这里捕获组保持空。
    match = re.fullmatch(
        r"/points/(?:shop/(?:app/([0-9]{1,12})/|c/(?:[a-z0-9_-]{1,40}/){1,4})?|profile/[0-9]{1,20}/)reward/([0-9]{1,12})/?",
        parsed.path,
    )
    if not match:
        raise ParseError("unsupported", "请打开 Steam 点数商店里的单个物品，再复制地址栏链接")
    return match[2], match[1] or ""


def shared_file_id(url: str) -> str:
    """社区作品页（艺术作品、截图、创意工坊）的 ID；其它社区链接返回空。"""
    parsed = urlparse(url)
    if parsed.hostname not in COMMUNITY_HOSTS or not re.fullmatch(
        r"/(?:sharedfiles|workshop)/filedetails/?", parsed.path
    ):
        return ""
    ident = (parse_qs(parsed.query).get("id") or [""])[0]
    return ident if re.fullmatch(r"[0-9]{1,20}", ident) else ""


def _clean(text: str) -> str:
    # 不少作品用 U+3164 之类的空白字符占位当标题
    return html.unescape(text).strip(" \t\r\n\u3164")


def _page_text(page: str, pattern: str) -> str:
    match = re.search(pattern, page)
    return _clean(match[1]) if match else ""


def asset_url(appid: int, name: str) -> str:
    if not name:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./-]{1,300}", name) or any(part in {".", "..", ""} for part in name.split("/")):
        raise ParseError("parse", "Steam 返回的素材地址不可用")
    return f"{CDN}/{appid}/{name}"


class Steam(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        if urlparse(share_url).hostname in COMMUNITY_HOSTS:
            return await self._community(share_url)
        return await self._reward(share_url)

    async def _community(self, share_url: str) -> VideoInfo:
        ident = shared_file_id(share_url)
        if not ident:
            # 社区视频、直播等其它页面沿用 yt-dlp
            return await YtDlp().parse_share_url(share_url)
        page_url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={ident}"
        page = await self._community_page(page_url)
        if not page:
            return await self._published_file(ident, page_url)
        images = list(dict.fromkeys(UGC_IMAGE.findall(page)))[:MAX_IMAGES]
        if not images:
            # 视频类作品只是 YouTube 外链，交给 yt-dlp 处理
            return await YtDlp().parse_share_url(page_url)
        return VideoInfo(
            video_url="",
            cover_url=images[0],
            title=_page_text(page, r'class="workshopItemTitle">([^<]*)') or "Steam",
            author=VideoAuthor(name=_page_text(page, r'class="friendBlockContent">\s*([^<]+?)\s*<br') or "Steam"),
            images=[ImgInfo(url=url) for url in images],
            page_url=page_url,
        )

    async def _community_page(self, page_url: str) -> str:
        """作品页 HTML；被限流时返回空串。"""
        # 社区页对不带 Accept-Language 的请求一律回 429，带上后才按正常访问处理
        headers = {"User-Agent": self.ua("windows"), "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        try:
            return await self.get_text(page_url, headers=headers, follow_redirects=True)
        except httpx.HTTPStatusError as exc:
            # 不存在、已删除或私密的作品，社区页直接回 500
            if exc.response.status_code in {404, 500}:
                raise ParseError("deleted", "Steam 上找不到这个作品") from exc
            if exc.response.status_code == 429:
                return ""
            raise

    async def _published_file(self, ident: str, page_url: str) -> VideoInfo:
        """社区页被限流时改查公开接口。接口只给创意工坊条目的首张预览图，艺术作品和截图查不到。"""
        data = await self.post_json(
            "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
            data={"itemcount": 1, "publishedfileids[0]": ident},
        )
        rows = (data.get("response") or {}).get("publishedfiledetails") or []
        item = next((row for row in rows if str(row.get("publishedfileid")) == ident and row.get("result") == 1), {})
        preview = item.get("preview_url") or ""
        if not UGC_IMAGE.fullmatch(preview):
            raise ParseError("blocked", "Steam 社区暂时限制了访问")
        return VideoInfo(
            video_url="",
            cover_url=preview,
            title=_clean(item.get("title") or "") or "Steam",
            author=VideoAuthor(name="Steam"),
            images=[ImgInfo(url=preview)],
            page_url=page_url,
        )

    async def _reward(self, share_url: str) -> VideoInfo:
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
