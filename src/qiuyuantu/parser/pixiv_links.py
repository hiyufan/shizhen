"""Pixiv 主站入口归一；限定码保留在链接中，不从普通 ID 猜测。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from .errors import ParseError

ROOT = "https://www.pixiv.net"
HOSTS = {"pixiv.net", "www.pixiv.net", "touch.pixiv.net"}
ID = r"[1-9][0-9]{0,19}"


@dataclass(frozen=True)
class PixivLink:
    kind: str
    ident: str = ""
    owner: str = ""
    section: str = ""
    page: int = 1
    query: tuple[tuple[str, str], ...] = ()

    def params(self) -> dict[str, str]:
        return dict(self.query)


def safe_media(url: str) -> str:
    if not isinstance(url, str):
        return ""
    p = urlparse(url)
    if p.scheme == "https" and p.hostname == "i.pximg.net" and not p.username and not p.port:
        return url
    return ""


def page_link(url: str, page: int) -> str:
    p = urlparse(url)
    query = parse_qs(p.query)
    query["p"] = [str(page)]
    return ROOT + p.path + "?" + urlencode({key: value[0] for key, value in query.items()})


def _legacy(path: str, query: dict) -> PixivLink | None:
    field, kind = {
        "/member_illust.php": ("illust_id", "artwork"),
        "/member.php": ("id", "user"),
        "/novel/show.php": ("id", "novel"),
    }.get(path, ("", ""))
    ident = query.get(field, [""])[0]
    if kind and re.fullmatch(ID, ident):
        return PixivLink(kind, ident)
    return None


def _user(path: str, page: int) -> PixivLink | None:
    match = re.fullmatch(rf"/(?:users|u)/({ID})(?:/(artworks|illustrations|manga|novels|collections))?/?", path)
    if match:
        return PixivLink("user", match[1], section=match[2] or "artworks", page=page)
    return None


def classify_link(url: str) -> PixivLink:
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or p.username or p.port:
        raise ParseError("unsupported", "请粘贴完整的 Pixiv 链接")
    if p.hostname == "pixiv.me" and re.fullmatch(r"/[A-Za-z0-9_-]+/?", p.path):
        return PixivLink("redirect")
    if p.hostname == "i.pximg.net":
        return PixivLink("file")
    if p.hostname not in HOSTS:
        raise ParseError("unsupported", "这个链接不是 Pixiv 主站内容")
    path = re.sub(r"^/[a-z]{2}(?:-[a-zA-Z]{2})?(?=/)", "", p.path)
    query = parse_qs(p.query)
    page = query.get("p", ["1"])[0]
    if not page.isdecimal() or not 1 <= int(page) <= 1000:
        raise ParseError("unsupported", "页码超过范围")
    if legacy := _legacy(path, query):
        return legacy
    if user := _user(path, int(page)):
        return user
    return _content_link(path, query, int(page))


def _content_link(path: str, query: dict, page: int) -> PixivLink:
    patterns = (
        ("artwork", rf"/(?:artworks|i)/({ID})/?"),
        ("novel", rf"/n/({ID})/?"),
        ("unlisted", r"/artworks/unlisted/([A-Za-z0-9_-]{10,128})/?"),
        ("novel_series", rf"/novel/series/({ID})/?"),
        ("collection", rf"/collections/({ID})/?"),
    )
    for kind, pattern in patterns:
        if match := re.fullmatch(pattern, path):
            return PixivLink(kind, match[1], page=page)
    if match := re.fullmatch(rf"/user/({ID})/series/({ID})/?", path):
        return PixivLink("manga_series", match[2], owner=match[1], page=page)
    if match := re.fullmatch(r"/tags/([^/]+)(?:/(artworks|illustrations|manga|ugoira|novels|collections))?/?", path):
        return PixivLink("search", unquote(match[1]), section=match[2] or "artworks", page=page, query=_filters(query))
    if path in {"/ranking.php", "/novel/ranking.php"}:
        return PixivLink(
            "ranking", section="novels" if path.startswith("/novel") else "artworks", page=page, query=_filters(query)
        )
    if path == "/search.php" and query.get("word"):
        return PixivLink("search", query["word"][0], section="artworks", page=page, query=_filters(query))
    if "/bookmarks" in path or path.startswith(("/bookmark", "/discovery", "/following")):
        raise ParseError("login", "这个目录当前无法匿名读取，请换成公开作品链接")
    raise ParseError("unsupported", "请粘贴作品、作者、系列、标签、排行榜或集合链接")


def _filters(query: dict) -> tuple[tuple[str, str], ...]:
    allowed = {"mode", "content", "date", "order", "s_mode", "type", "scd", "ecd"}
    return tuple((key, values[0][:100]) for key, values in query.items() if key in allowed)
