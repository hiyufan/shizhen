"""音乐封面：只读取公开的歌曲 / 专辑 / 歌单信息，不获取歌曲播放地址。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
from parsel import Selector

from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

_KINDS = {"song", "album", "playlist"}
_QQ_PATHS = {"song": "songDetail", "album": "albumDetail", "playlist": "playlist"}


def _host_in(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _identifier(value: str, *, numeric: bool = False) -> str:
    pattern = r"[0-9]{1,20}" if numeric else r"[A-Za-z0-9]{1,64}"
    if not re.fullmatch(pattern, value):
        raise ParseError("unsupported", "音乐链接里没有有效的作品 ID")
    return value


def _legacy_id(value: str) -> tuple[str, str]:
    kind, separator, ident = value.partition(":")
    kind, ident = (kind, ident) if separator else ("song", value)
    if kind not in _KINDS:
        raise ParseError("unsupported", "请使用歌曲、专辑或歌单链接")
    return kind, ident


async def _expand_short(parser: BaseParser, url: str, hosts: tuple[str, ...]) -> str:
    if (urlparse(url).hostname or "").lower() in hosts:
        response = await parser.fetch("GET", url, follow_redirects=True)
        return str(response.url)
    return url


def _cover_info(title: str, cover: str, author: VideoAuthor, page_url: str) -> VideoInfo:
    if not cover:
        raise ParseError("empty", "没有找到音乐封面")
    return VideoInfo(
        video_url="",
        cover_url=cover,
        title=title or "音乐封面",
        author=author,
        images=[ImgInfo(url=cover)],
        page_url=page_url,
    )


def netease_target(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not _host_in((parsed.hostname or "").lower(), ("music.163.com",)):
        raise ParseError("unsupported", "不是网易云音乐的作品链接")
    # 桌面版 #/playlist?id=… 的参数位于 fragment，手机分享页还有 /m/ 前缀。
    target = urlparse(parsed.fragment) if parsed.fragment.startswith("/") else parsed
    match = re.fullmatch(r"/(?:m/)?(song|album|playlist)(?:/([0-9]+))?/?", target.path)
    if not match:
        raise ParseError("unsupported", "请使用网易云音乐的歌曲、专辑或歌单链接")
    ident = match[2] or parse_qs(target.query).get("id", [""])[0]
    return match[1], _identifier(ident, numeric=True)


def netease_cover(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not _host_in(parsed.hostname or "", ("music.126.net",)):
        raise ParseError("empty", "网易云音乐没有提供有效的封面地址")
    # param / thumbnail 是缩略图参数。保留原文件，歌单封面可能实际为动态 WebP。
    return urlunparse(parsed._replace(scheme="https", query="", fragment=""))


def _netease_detail(data: dict, kind: str) -> tuple[str, str, VideoAuthor]:
    if data.get("code") != 200:
        reason = {404: "deleted", 301: "login", 401: "login", 403: "blocked", -460: "blocked", -462: "blocked"}
        raise ParseError(reason.get(data.get("code"), "parse"), "网易云音乐暂时没有返回作品信息")
    if kind == "song":
        detail = next(iter(data.get("songs") or []), {})
        cover = (detail.get("album") or {}).get("picUrl") or ""
    else:
        detail = data.get(kind) or {}
        cover = detail.get("coverImgUrl" if kind == "playlist" else "picUrl") or ""
    if not detail:
        raise ParseError("deleted", "音乐作品不存在或不可公开访问")
    creator = detail.get("creator") or {}
    artists = detail.get("artists") or [detail.get("artist") or {}]
    author = VideoAuthor(
        uid=str(creator.get("userId") or ""),
        name=creator.get("nickname") or " / ".join(artist.get("name") or "" for artist in artists).strip(" /"),
        avatar=creator.get("avatarUrl") or "",
    )
    return detail.get("name") or "", cover, author


class NetEaseMusic(BaseParser):
    """网易云音乐公开信息接口：歌曲用专辑封面，歌单只取自身封面。"""

    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua("windows"), "Referer": "https://music.163.com/"}

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        url = await _expand_short(self, share_url, ("163cn.tv",))
        kind, ident = netease_target(url)
        paths = {
            "song": f"/api/song/detail?ids=%5B{ident}%5D",
            "album": f"/api/v1/album/{ident}",
            "playlist": f"/api/v6/playlist/detail?id={ident}&n=0",
        }
        data = await self.get_json("https://music.163.com" + paths[kind])
        title, cover, author = _netease_detail(data, kind)
        return _cover_info(title, netease_cover(cover), author, f"https://music.163.com/{kind}?id={ident}")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        kind, ident = _legacy_id(video_id)
        return await self.parse_share_url(f"https://music.163.com/{kind}?id={_identifier(ident, numeric=True)}")


def qq_target(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not _host_in((parsed.hostname or "").lower(), ("y.qq.com",)):
        raise ParseError("unsupported", "不是 QQ 音乐的作品链接")
    match = re.fullmatch(
        r"/n/(?:ryqq(?:_v2)?|yqq)/(songDetail|albumDetail|song|album|playlist)/([A-Za-z0-9]+)(?:\.html)?/?", parsed.path
    )
    if match:
        kind = ({value: key for key, value in _QQ_PATHS.items()} | {key: key for key in _KINDS})[match[1]]
        return kind, _identifier(match[2], numeric=kind == "playlist")
    return _qq_query_target(parsed.path, parse_qs(parsed.query))


def _qq_query_target(path: str, query: dict[str, list[str]]) -> tuple[str, str]:
    # App 的分享页使用 taoge / album / song.html；旧桌面详情页也使用 query ID。
    routes = {
        "/n2/m/share/details/taoge.html": ("playlist", ("id", "disstid")),
        "/n2/m/share/details/album.html": ("album", ("albummid", "albumid", "id")),
        "/n2/m/share/details/song.html": ("song", ("songmid", "songid", "id")),
        "/n/yqq/playlist.html": ("playlist", ("id", "disstid")),
        "/n/yqq/album.html": ("album", ("albummid", "albumid", "id")),
        "/n/yqq/song.html": ("song", ("songmid", "songid", "id")),
        "/v8/playsong.html": ("song", ("songmid", "songid", "id")),
    }
    if path not in routes:
        raise ParseError("unsupported", "请使用 QQ 音乐的歌曲、专辑或歌单链接")
    kind, keys = routes[path]
    ident = next((query[key][0] for key in keys if query.get(key)), "")
    return kind, _identifier(ident, numeric=kind == "playlist")


def qq_cover(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not _host_in(parsed.hostname or "", ("y.qq.com", "y.gtimg.cn")):
        raise ParseError("empty", "QQ 音乐没有提供有效的封面地址")
    path = parsed.path
    host = parsed.netloc
    if path.startswith("/music/photo_new/"):
        host = "y.gtimg.cn"
        path = re.sub(r"R([0-9]+)x([0-9]+)", lambda m: f"R{max(800, int(m[1]))}x{max(800, int(m[2]))}", path)
    # /0 在 QQ 音乐并不代表原图，会返回 120px。公开歌单的 /1000 可取高清封面。
    elif path.startswith("/music_cover/"):
        path = re.sub(r"/([0-9]+)$", lambda m: f"/{max(1000, int(m[1]))}", path)
    return urlunparse(parsed._replace(scheme="https", netloc=host, path=path, query="", fragment=""))


def _qq_cover_candidates(cover: str) -> list[str]:
    match = re.search(r"/T002R([0-9]+)x([0-9]+)M000", cover)
    if not match:
        return []
    original = re.sub(r"T002R[0-9]+x[0-9]+M000", "T002M000", cover)
    variants = [
        re.sub(r"R[0-9]+x[0-9]+", f"R{size}x{size}", cover)
        for size in (1500, 1200)
        if size > max(int(match[1]), int(match[2]))
    ]
    return [original, *variants]


class QQMusic(BaseParser):
    """QQ 音乐服务端渲染页中的公开 Open Graph 信息。"""

    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua("windows"), "Referer": "https://y.qq.com/"}

    async def _best_cover(self, url: str) -> str:
        cover = qq_cover(url)
        # 无 R 尺寸段的是源文件；实测同一专辑为 2048px，而网页默认只给 300px。
        # 部分旧封面没有该路径，验证成功才替换，不能返回一个猜出来的 404 地址。
        for candidate in _qq_cover_candidates(cover):
            try:
                response = await self.fetch("HEAD", candidate, timeout=5)
            except httpx.HTTPError:
                continue
            if response.headers.get("content-type", "").startswith("image/"):
                return candidate
        return cover

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        url = await _expand_short(self, share_url, ("c6.y.qq.com", "c.y.qq.com"))
        kind, ident = qq_target(url)
        canonical = f"https://y.qq.com/n/ryqq_v2/{_QQ_PATHS[kind]}/{ident}"
        html = await self.get_text(canonical, follow_redirects=True)
        sel = Selector(html)
        cover = sel.css('meta[property="og:image"]::attr(content)').get(default="")
        if not cover:
            raise ParseError("empty", "QQ 音乐页面没有封面，作品可能已下架或不可公开访问")
        title = sel.css('meta[property="og:title"]::attr(content)').get(default="")
        title = re.sub(r"\s*-\s*QQ音乐.*$", "", title).strip()
        author = VideoAuthor(name=sel.css('meta[property="og:music:artist"]::attr(content)').get(default=""))
        return _cover_info(title, await self._best_cover(cover), author, canonical)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        kind, ident = _legacy_id(video_id)
        return await self.parse_share_url(f"https://y.qq.com/n/ryqq_v2/{_QQ_PATHS[kind]}/{_identifier(ident)}")
