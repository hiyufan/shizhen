"""HoYoPlay 当前官方启动器壁纸，游戏入口与公开接口共享同一份映射。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

API = "https://hyp-api.mihoyo.com/hyp/hyp-connect/api/getAllGameBasicInfo"
GAMES = {
    "genshin": ("原神", "1Z8W5NHUQb", "hk4e_cn"),
    "star-rail": ("崩坏：星穹铁道", "64kMb5iAWu", "hkrpg_cn"),
    "zenless": ("绝区零", "x6znKlJ0xK", "nap_cn"),
    "honkai3": ("崩坏 3", "osvnlOc0S8", "bh3_cn"),
}


def game_url(game: str) -> str:
    if game not in GAMES:
        raise ParseError("unsupported", "请选择支持的 HoYoPlay 游戏")
    return API + "?" + urlencode({"launcher_id": "jGHBHlcOq1", "language": "zh-cn", "game_id": GAMES[game][1]})


def game_from_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "hyp-api.mihoyo.com"
        or parsed.path != urlparse(API).path
    ):
        raise ParseError("unsupported", "请在 HoYoPlay 壁纸工具中选择游戏")
    ident = parse_qs(parsed.query).get("game_id", [""])[0]
    game = next((slug for slug, (_, game_id, _) in GAMES.items() if ident == game_id), "")
    if not game:
        raise ParseError("unsupported", "请选择支持的 HoYoPlay 游戏")
    return game


def _asset(background: dict, key: str) -> str:
    value = (background.get(key) or {}).get("url") or ""
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "launcher-webstatic.mihoyo.com":
        raise ParseError("parse", "HoYoPlay 返回的壁纸地址不可用")
    return value


class HoYoPlay(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(game_from_url(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        url = game_url(video_id)
        name, _, biz = GAMES[video_id]
        data = await self.get_json(url)
        if data.get("retcode") != 0:
            raise ParseError("parse", "HoYoPlay 暂时没有返回壁纸信息")
        rows = (data.get("data") or {}).get("game_info_list") or []
        backgrounds = [
            bg for row in rows if (row.get("game") or {}).get("biz") == biz for bg in row.get("backgrounds") or []
        ]
        images = list(
            dict.fromkeys(u for bg in backgrounds for key in ("background", "theme") if (u := _asset(bg, key)))
        )
        videos = list(dict.fromkeys(u for bg in backgrounds if (u := _asset(bg, "video"))))
        if not images:
            raise ParseError("empty", "这个游戏当前没有可用的公开壁纸")
        return VideoInfo(
            video_url=videos[0] if videos else "",
            cover_url=images[0],
            title=name + " · HoYoPlay",
            images=[ImgInfo(url=u) for u in images],
            author=VideoAuthor(name="HoYoPlay"),
            page_url=url,
            formats=[FormatInfo(label=f"WebM {i + 2}", url=u, ext="webm") for i, u in enumerate(videos[1:])],
        )
