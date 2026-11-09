"""Steam 链接归类：商店、点数商店与社区各自的入口，归不进去的给出能照着做的提示。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .errors import ParseError

STORE_HOST = "store.steampowered.com"
COMMUNITY_HOSTS = ("steamcommunity.com", "www.steamcommunity.com")
SHORT_HOST = "s.team"
POINTS_HINT = "请打开 Steam 点数商店里的单个物品或某个游戏的物品页，再复制地址栏链接"
STORE_HINT = "请粘贴 Steam 游戏商店页、点数商店物品或社区作品的链接"
INVENTORY_HINT = "Steam 库存页读不到单个物品；点数商店的物品请打开它在点数商店里的页面"
MARKET_HINT = "Steam 市场物品暂不支持"


@dataclass(frozen=True)
class SteamLink:
    # reward / points_app / app / shared / profile / community / short
    kind: str
    ident: str = ""
    app: str = ""


def reward_id(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or parsed.hostname != STORE_HOST:
        raise ParseError("unsupported", "请使用 Steam 点数商店的单个物品链接")
    match = _reward(parsed.path)
    if not match:
        raise ParseError("unsupported", "请打开 Steam 点数商店里的单个物品，再复制地址栏链接")
    return match[2], match[1] or ""


def _reward(path: str) -> re.Match | None:
    # 点数商店从个人点数页进入某个奖励时地址是 /points/profile/<steamid64>/reward/<defid>，
    # 其中的 steamid64 不是 appid，appid 仍由接口返回，这里捕获组保持空。
    return re.fullmatch(
        r"/points/(?:shop/(?:app/([0-9]{1,12})/|c/(?:[a-z0-9_-]{1,40}/){1,4})?|profile/[0-9]{1,20}/)reward/([0-9]{1,12})/?",
        path,
    )


def shared_file_id(url: str) -> str:
    """社区作品页（艺术作品、截图、创意工坊）的 ID；其它社区链接返回空。"""
    parsed = urlparse(url)
    if parsed.hostname not in COMMUNITY_HOSTS or not re.fullmatch(
        r"/(?:sharedfiles|workshop)/filedetails/?", parsed.path
    ):
        return ""
    ident = (parse_qs(parsed.query).get("id") or [""])[0]
    return ident if re.fullmatch(r"[0-9]{1,20}", ident) else ""


def classify_link(url: str) -> SteamLink:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.port:
        raise ParseError("unsupported", "请粘贴完整的 Steam 链接")
    if parsed.hostname == SHORT_HOST:
        return SteamLink("short")
    if parsed.hostname in COMMUNITY_HOSTS:
        return _community(url, parsed.path)
    if parsed.hostname == STORE_HOST:
        return _store(parsed.path)
    raise ParseError("unsupported", STORE_HINT)


def _store(path: str) -> SteamLink:
    if match := _reward(path):
        return SteamLink("reward", match[2], match[1] or "")
    if match := re.fullmatch(r"/points/shop/app/([0-9]{1,12})/?", path):
        return SteamLink("points_app", match[1])
    if match := re.fullmatch(r"/points/profile/([0-9]{17})/?", path):
        # 个人点数页展示的就是这个用户正在使用的头像、头像框和背景
        return SteamLink("profile", "profiles/" + match[1])
    if match := re.fullmatch(r"(?:/agecheck)?/app/([0-9]{1,12})(?:/[^/]*)?/?", path):
        return SteamLink("app", match[1])
    raise ParseError("unsupported", POINTS_HINT if path.startswith("/points") else STORE_HINT)


def _community(url: str, path: str) -> SteamLink:
    if ident := shared_file_id(url):
        return SteamLink("shared", ident)
    if match := re.fullmatch(r"/(id/[A-Za-z0-9_-]{2,32}|profiles/[0-9]{17})/?", path):
        return SteamLink("profile", match[1])
    if re.match(r"/(?:id|profiles)/[^/]+/inventory", path):
        raise ParseError("unsupported", INVENTORY_HINT)
    if path.startswith("/market/"):
        raise ParseError("unsupported", MARKET_HINT)
    # 社区视频、直播等页面由 yt-dlp 处理
    return SteamLink("community")
