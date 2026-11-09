"""Steam 社区作品与个人资料的公开素材；不读取库存、好友或任何需要登录的数据。"""

from __future__ import annotations

import contextlib
import html
import re

import httpx

from .base import ImgInfo, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError, classify
from .ytdlp import YtDlp

COMMUNITY_ROOT = "https://steamcommunity.com"
COMMUNITY_IMAGES = "https://shared.fastly.steamstatic.com/community_assets/images/"
COMMUNITY_HINT = "请粘贴 Steam 社区里的单个作品、截图、视频或个人资料链接"
EQUIPPED_API = "https://api.steampowered.com/IPlayerService/GetProfileItemsEquipped/v1/"
# 艺术作品、截图和创意工坊预览图都放在 ugc/<内容 ID>/<SHA1>/；不带缩放参数就是上传时的原文件（GIF 保留动画）。
# 这个地址只响应 GET，HEAD 会返回 404。
UGC_IMAGE = re.compile(r"https://images\.steamusercontent\.com/ugc/[0-9]{1,20}/[0-9A-Fa-f]{40}/")
AVATAR = re.compile(r"https://avatars\.[a-z]+\.steamstatic\.com/[0-9a-f]{40}_full\.(?:jpg|png|gif)")
MAX_IMAGES = 50
# (接口字段, 素材名, 动态素材在哪个字段)：头像 GIF 和头像框 APNG 在 small，背景的 large 是静态预览
PROFILE_ITEMS = (
    ("animated_avatar", "动态头像", "image_small"),
    ("avatar_frame", "头像框", "image_small"),
    ("profile_background", "个人资料背景", "image_large"),
    ("mini_profile_background", "迷你资料背景", "image_large"),
)


def _clean(text: str) -> str:
    # 不少作品用 U+3164 之类的空白字符占位当标题
    return html.unescape(text).strip(" \t\r\nㅤ")


def _page_text(page: str, pattern: str) -> str:
    match = re.search(pattern, page)
    return _clean(match[1]) if match else ""


def community_asset(path: object) -> str:
    path = str(path or "")
    if not re.fullmatch(r"items/[0-9]{1,12}/[0-9a-f]{40}\.(?:jpg|png|gif|webm|mp4)", path):
        return ""
    return COMMUNITY_IMAGES + path


def _profile_asset(item: dict, label: str, still_key: str) -> MediaAsset | None:
    image = community_asset(item.get(still_key))
    video = community_asset(item.get("movie_mp4")) or community_asset(item.get("movie_webm"))
    if video:
        return MediaAsset(video, label, "video", image, video.rsplit(".", 1)[-1])
    if image:
        animated = still_key == "image_small" or image.endswith(".gif")
        return MediaAsset(image, label, "animation" if animated else "image", image)
    return None


class SteamCommunity:
    def community_headers(self) -> dict[str, str]:
        # 社区页对不带 Accept-Language 的请求一律回 429，带上后才按正常访问处理
        return {"User-Agent": self.ua("windows"), "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

    async def community_video(self, share_url: str) -> VideoInfo:
        """社区视频、直播等页面沿用 yt-dlp；它也不认的页面换成能照着做的提示。"""
        try:
            return await YtDlp().parse_share_url(share_url)
        except Exception as exc:
            if classify(exc).reason == "unsupported":
                raise ParseError("unsupported", COMMUNITY_HINT) from exc
            raise

    async def shared_file(self, ident: str) -> VideoInfo:
        page_url = f"{COMMUNITY_ROOT}/sharedfiles/filedetails/?id={ident}"
        page = await self._community_page(page_url, "Steam 上找不到这个作品")
        if not page:
            return await self._published_file(ident, page_url)
        images = list(dict.fromkeys(UGC_IMAGE.findall(page)))[:MAX_IMAGES]
        if not images:
            # 视频类作品只是 YouTube 外链，交给 yt-dlp 处理
            return await self.community_video(page_url)
        return VideoInfo(
            video_url="",
            cover_url=images[0],
            title=_page_text(page, r'class="workshopItemTitle">([^<]*)') or "Steam",
            author=VideoAuthor(name=_page_text(page, r'class="friendBlockContent">\s*([^<]+?)\s*<br') or "Steam"),
            images=[ImgInfo(url=url) for url in images],
            page_url=page_url,
        )

    async def _community_page(self, page_url: str, missing: str) -> str:
        """社区页 HTML；被限流时返回空串。"""
        try:
            return await self.get_text(page_url, headers=self.community_headers(), follow_redirects=True)
        except httpx.HTTPStatusError as exc:
            # 不存在、已删除或私密的内容，社区页直接回 500
            if exc.response.status_code in {404, 500}:
                raise ParseError("deleted", missing) from exc
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

    async def profile(self, path: str) -> VideoInfo:
        """个人资料上的头像与正在展示的点数商店物品（动态头像、头像框、背景）。"""
        page_url = f"{COMMUNITY_ROOT}/{path}/"
        page = await self._community_page(page_url + "?xml=1", "Steam 上找不到这个用户")
        steamid = re.search(r"<steamID64>([0-9]{17})</steamID64>", page)
        if not steamid:
            raise ParseError("blocked" if not page else "deleted", "Steam 没有返回这个用户的公开资料")
        name = _page_text(page, r"<steamID><!\[CDATA\[(.*?)\]\]>") or steamid[1]
        avatar = re.search(r"<avatarFull><!\[CDATA\[(.*?)\]\]>", page)
        assets = await self._equipped(steamid[1])
        if avatar and AVATAR.fullmatch(avatar[1]):
            assets.insert(0, MediaAsset(avatar[1], "头像", "image", avatar[1]))
        if not assets:
            raise ParseError("empty", "这个用户的资料上没有可下载的头像或物品")
        return VideoInfo(
            "",
            assets[0].cover_url,
            title=name,
            author=VideoAuthor(uid=steamid[1], name=name),
            assets=assets,
            page_url=f"{COMMUNITY_ROOT}/profiles/{steamid[1]}/",
        )

    async def _equipped(self, steamid: str) -> list[MediaAsset]:
        # 接口失败时仍可给出头像
        with contextlib.suppress(httpx.HTTPError, ValueError):
            data = await self.get_json(EQUIPPED_API, params={"steamid": steamid, "language": "schinese"})
            equipped = data.get("response") or {}
            return [
                asset
                for key, label, still in PROFILE_ITEMS
                if isinstance(equipped.get(key), dict) and (asset := _profile_asset(equipped[key], label, still))
            ]
        return []
