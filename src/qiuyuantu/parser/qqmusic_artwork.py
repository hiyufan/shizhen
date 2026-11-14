"""QQ 音乐公开专辑动态封面；只读取封面 VID，不获取歌曲或 MV。"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from .base import BaseParser

_GATEWAY = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_COVER_MID = re.compile(r"/music/photo_new/T002(?:R[0-9]+x[0-9]+)?M000([A-Za-z0-9]+)(?:_[0-9]+)?\.jpg$")


async def _request(parser: BaseParser, module: str, method: str, param: dict) -> dict:
    data = await parser.post_json(
        _GATEWAY,
        json={"comm": {"ct": 24, "cv": 0}, "req": {"module": module, "method": method, "param": param}},
        timeout=10,
    )
    if not isinstance(data, dict) or not isinstance(data.get("req"), dict):
        raise ValueError("QQ 音乐动态封面信息不完整")
    reply = data["req"]
    if data.get("code") != 0 or reply.get("code") != 0:
        raise ValueError("QQ 音乐暂时没有返回动态封面信息")
    result = reply.get("data")
    if not isinstance(result, dict):
        raise ValueError("QQ 音乐动态封面信息不完整")
    return result


def _video_url(value: str) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not host.endswith(".stream.tencentmusic.com")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 80, 443}
        or not parsed.path.endswith(".mp4")
    ):
        return ""
    return urlunparse(parsed._replace(scheme="https", netloc=host, fragment=""))


def _best_video(data: dict, vid: str, *, artwork_only: bool = False) -> str:
    detail = data.get(vid) or {}
    if not isinstance(detail, dict):
        raise ValueError("QQ 音乐动态封面信息不完整")
    # 专辑头图也可能放宣传片；补取时只收网关标为封面素材（videoType=8）的有限短片。
    duration = detail.get("duration")
    if artwork_only and (not isinstance(duration, (int, float)) or not 0 < duration <= 120):
        return ""
    files = detail.get("mp4") or []
    if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
        raise ValueError("QQ 音乐动态封面档位无效")
    # filetype 是平台的档位编号；只在成功且有真实地址的档位里选，空的高档位不能盖过可用视频。
    for item in sorted(files, key=lambda row: int(row.get("filetype") or 0), reverse=True):
        if item.get("code") != 0 or (artwork_only and item.get("videoType") != 8):
            continue
        for value in item.get("freeflow_url") or []:
            if url := _video_url(value):
                return url
    return ""


def _cover_candidates(basic: dict) -> list[tuple[str, bool]]:
    """动态专用字段优先；部分专辑只把封面动画放在头图，头图另核视频类型与时长。"""
    primary = basic.get("dynamicCoverVid") or ""
    if primary and (not isinstance(primary, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", primary)):
        raise ValueError("QQ 音乐动态封面 ID 无效")
    candidates = [(primary, False)] if primary else []
    media = basic.get("headMediaList") or []
    headers = [basic.get("headVideoVid")]
    if isinstance(media, list):
        headers += [row.get("vid") for row in media[:4] if isinstance(row, dict)]
    seen = {primary}
    for vid in headers:
        if isinstance(vid, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", vid) and vid not in seen:
            candidates.append((vid, True))
            seen.add(vid)
    return candidates[:4]


async def dynamic_cover(parser: BaseParser, cover: str) -> str:
    match = _COVER_MID.fullmatch(urlparse(cover).path)
    if not match:
        return ""
    album = await _request(parser, "music.musichallAlbum.AlbumInfoServer", "GetAlbumDetail", {"albumMid": match[1]})
    basic = album.get("basicInfo")
    if not isinstance(basic, dict):
        raise ValueError("QQ 音乐专辑信息不完整")
    candidates = _cover_candidates(basic)
    if not candidates:
        return ""
    data = await _request(parser, "music.stream.MvUrlProxy", "GetMvUrls", {"vids": [vid for vid, _ in candidates]})
    for vid, artwork_only in candidates:
        if url := _best_video(data, vid, artwork_only=artwork_only):
            return url
    return ""
