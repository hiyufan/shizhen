"""华为音乐公开封面；动态素材来自 picture.dynamicImgURL，不读取歌曲播放文件。"""

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .base import BaseParser, ContentEntry, ImgInfo, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError

HOST = "portal-drcn.music.dbankcloud.cn"
SHORT_HOST = "url.cloud.huawei.com"
ORIGIN = f"https://{HOST}"
PAGE = ORIGIN + "/music-apph5-service/h5/index.html"
_KINDS = {"1": "song", "2": "album", "4": "playlist", "5": "playlist"}
_IMAGE_HOSTS = {"contentcenter-drcn.dbankcdn.cn", "ccimg-drcn.dbankcdn.cn", "image.tingmall.com"}
_PAGE_SIZE = 20


def _identifier(value: str) -> str:
    # 华为自有编号除数字外还有带 -、_、. 的不透明编号，不能转为整数。
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", value):
        raise ParseError("unsupported", "华为音乐链接里没有有效的作品 ID")
    return value


def target(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    route = urlparse(parsed.fragment)
    match = re.fullmatch(r"/musicshare/([1245])/?", route.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != HOST
        or parsed.path != "/music-apph5-service/h5/index.html"
        or not match
    ):
        raise ParseError("unsupported", "请使用华为音乐的歌曲、专辑或歌单分享链接")
    query = parse_qs(route.query)
    kind = _KINDS[match[1]]
    ident = query.get("listid", [""])[0] if kind == "playlist" else ""
    ident = _identifier(ident or query.get("id", [""])[0])
    start = query.get("start", ["0"])[0]
    if not re.fullmatch(r"[0-9]{1,5}", start) or int(start) > 10000 or (kind != "playlist" and start != "0"):
        raise ParseError("unsupported", "华为音乐目录页码无效")
    return kind, ident, int(start)


def page_url(kind: str, ident: str, start: int = 0) -> str:
    number = {"song": "1", "album": "2", "playlist": "4"}[kind]
    query = {"id": ident}
    if start:
        query["start"] = str(start)
    return PAGE + f"#/musicshare/{number}?" + urlencode(query)


def cover_url(value) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc not in _IMAGE_HOSTS or parsed.fragment:
        return ""
    return value


def is_dynamic_cover(value: str) -> bool:
    parsed = urlparse(value)
    return bool(
        cover_url(value)
        and parsed.hostname == "contentcenter-drcn.dbankcdn.cn"
        and re.fullmatch(r"/img/pub_1/HW-Music_DynamicCover_100_1/[A-Za-z0-9_./-]+\.webp", parsed.path)
    )


def _picture(info: dict) -> dict:
    value = info.get("picture")
    return value if isinstance(value, dict) else {}


def _static_cover(info: dict) -> str:
    picture = _picture(info)
    return next(
        (url for key in ("bigImgURL", "middleImgURL", "smallImgURL") if (url := cover_url(picture.get(key)))), ""
    )


def _dynamic_cover(info: dict) -> str:
    value = _picture(info).get("dynamicImgURL", "")
    return value if isinstance(value, str) and is_dynamic_cover(value) else ""


def _songs(value) -> list[dict]:
    return [row for row in value[:_PAGE_SIZE] if isinstance(row, dict)] if isinstance(value, list) else []


def _album_dynamic(info: dict, songs: list[dict]) -> str:
    if dynamic := _dynamic_cover(info):
        return dynamic
    # 专辑接口可能只在曲目上返回动图。仅接受本专辑且静态封面相同的曲目，不借用推荐曲或另一张封面。
    static = _static_cover(info)
    for song in songs:
        if (
            song.get("albumID") == info.get("contentID")
            and static
            and _static_cover(song) == static
            and (dynamic := _dynamic_cover(song))
        ):
            return dynamic
    return ""


def _media_info(info: dict, page: str, dynamic: str) -> VideoInfo:
    static = _static_cover(info)
    if not static:
        raise ParseError("empty", "华为音乐没有提供可用的封面")
    assets = (
        [MediaAsset(url=dynamic, label="动态封面 WebP", kind="animation", cover_url=dynamic, ext="webp")]
        if dynamic
        else []
    )
    return VideoInfo(
        video_url="",
        cover_url=static,
        title=info.get("contentName") or "音乐封面",
        author=VideoAuthor(name=info.get("artistName") or info.get("subTitle") or ""),
        images=[ImgInfo(url=static)],
        assets=assets,
        page_url=page,
        notice="" if dynamic else "这条作品当前未公开动态封面，可下载静态封面。",
    )


def _directory(info: dict, songs: list[dict], ident: str, start: int) -> VideoInfo:
    entries = []
    for song in songs:
        code = song.get("contentID", "")
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", code):
            entries.append(
                ContentEntry(
                    url=page_url("song", code),
                    title=song.get("contentName") or "音乐封面",
                    # 目录只加载静态预览，避免一次加载多份十几 MB 的原始动图。
                    cover_url=cover_url(_picture(song).get("smallImgURL")) or _static_cover(song),
                )
            )
    total = str(info.get("totalCount", "0"))
    total = int(total) if re.fullmatch(r"[0-9]{1,7}", total) else 0
    more = len(songs) == _PAGE_SIZE and (not total or start + len(songs) < total) and start + len(songs) <= 10000
    return VideoInfo(
        video_url="",
        cover_url=_static_cover(info),
        title=info.get("contentName") or "音乐封面",
        content_kind="directory",
        entries=entries,
        entry_total=total,
        page_url=page_url("playlist", ident, start),
        next_page_url=page_url("playlist", ident, start + len(songs)) if more else "",
    )


class HuaweiMusic(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        for _ in range(4):
            parsed = urlparse(share_url)
            if parsed.scheme != "https" or parsed.netloc != SHORT_HOST:
                break
            if not re.fullmatch(r"/[A-Za-z0-9]{1,64}", parsed.path):
                raise ParseError("unsupported", "请使用华为音乐的歌曲、专辑或歌单分享链接")
            location = await self.redirect_target(share_url)
            if not location:
                break
            share_url = urljoin(share_url, location)
        return await self._parse(*target(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        kind, separator, ident = video_id.partition(":")
        kind, ident = (kind, ident) if separator else ("song", video_id)
        if kind not in {"song", "album", "playlist"}:
            raise ParseError("unsupported", "请使用华为音乐的歌曲、专辑或歌单分享链接")
        return await self._parse(kind, _identifier(ident), 0)

    async def _parse(self, kind: str, ident: str, start: int) -> VideoInfo:
        if kind == "song":
            data = await self.get_json(
                ORIGIN + "/music-metacontent-service/v1/service/song/detail/bysongcode",
                params={"songCode": ident},
                timeout=15,
            )
            info, songs = data.get("songInfo", {}), []
        elif kind == "album":
            data = await self.get_json(
                ORIGIN + "/music-odpapp-service/v2/service/album/detail/byalbumcode",
                params={"contentCode": ident, "contentType": "2", "start": 0, "limit": _PAGE_SIZE},
                timeout=15,
            )
            detail = data.get("albumInfoEx") or {}
            info, songs = detail.get("albumInfo", {}), _songs(detail.get("songSimpleInfos"))
        else:
            data = await self.post_json(
                ORIGIN + "/music-operation-service/v2/service/musiclist/detail/bymusiclistid",
                json={"musicListID": ident, "start": start, "limit": _PAGE_SIZE},
                timeout=15,
            )
            detail = data.get("musicListInfoEx") or {}
            info, songs = detail.get("musicListDetail", {}), _songs(detail.get("songSimpleInfos"))
        if (data.get("result") or {}).get("resultCode") != "000000":
            raise ParseError("restricted", "华为音乐暂时没有公开这条作品的信息")
        if not isinstance(info, dict) or info.get("contentID") != ident:
            raise ParseError("empty", "华为音乐没有返回对应的作品")
        if kind == "playlist":
            return _directory(info, songs, ident, start)
        dynamic = _album_dynamic(info, songs) if kind == "album" else _dynamic_cover(info)
        return _media_info(info, page_url(kind, ident), dynamic)
