"""QQ 音乐公开专辑动态封面；只读取封面 VID，不获取歌曲或 MV。"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from ..utils import gather_or_cancel
from . import mp4_probe
from .base import BaseParser

_GATEWAY = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_COVER_MID = re.compile(
    r"/music/photo_new/T002(?:R[0-9]+x[0-9]+)?M000([A-Za-z0-9]+)(?:_[0-9]+)?\.(?:jpg|jpeg|png|webp|avif)$"
)


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
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not host.endswith(".stream.tencentmusic.com")
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or not parsed.path.endswith(".mp4")
    ):
        return ""
    return urlunparse(parsed._replace(scheme="https", netloc=host, fragment=""))


def _file_rank(item: dict) -> int:
    value = item.get("filetype")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,4}", value):
        return int(value)
    return -1


def _file_url(item: dict) -> str:
    # 网关同时定义流量优惠、普通和公共地址；逐个校验，不能因首个地址失效就漏掉同档备用。
    for key in ("freeflow_url", "url", "comm_url", "validUrl"):
        values = item.get(key)
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            if url := _video_url(value):
                return url
    return ""


def _best_video(data: dict, vid: str, *, artwork_only: bool = False) -> str:
    detail = data.get(vid) or {}
    if not isinstance(detail, dict):
        return ""
    # 专辑头图也可能放宣传片；补取时只收网关标为封面素材（videoType=8）的有限短片。
    duration = detail.get("duration")
    if artwork_only and (type(duration) not in {int, float} or not 0 < duration <= 120):
        return ""
    files = detail.get("mp4") or []
    if not isinstance(files, list):
        return ""
    # filetype 是平台的档位编号；只在成功且有真实地址的档位里选，空的高档位不能盖过可用视频。
    for item in sorted((row for row in files if isinstance(row, dict)), key=_file_rank, reverse=True):
        if _file_rank(item) < 0 or item.get("code") != 0 or (artwork_only and item.get("videoType") != 8):
            continue
        if url := _file_url(item):
            return url
    return ""


def _valid_vid(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", value) is not None


def _cover_candidates(basic: dict) -> list[tuple[str, bool]]:
    """动态专用字段优先；部分专辑只把封面动画放在头图，头图另核视频类型与时长。"""
    primary = basic.get("dynamicCoverVid") or ""
    candidates = [(primary, False)] if _valid_vid(primary) else []
    media = basic.get("headMediaList") or []
    headers = [basic.get("headVideoVid")]
    if isinstance(media, list):
        # 先去掉空值与重复项再限制 VID 数，避免占位项挤掉后面的真实封面；扫描本身也有界。
        headers += [row.get("vid") for row in media[:32] if isinstance(row, dict)]
    seen = {vid for vid, _ in candidates}
    for vid in headers:
        if _valid_vid(vid) and vid not in seen:
            candidates.append((vid, True))
            seen.add(vid)
    return candidates[:4]


async def _square_url(urls: list[str], parser: BaseParser) -> str:
    """原生方形素材免去竖版裁切；只在实测尺寸、帧率都不低于默认源时采用。"""
    if len(urls) == 1:
        return urls[0]
    measured = await gather_or_cancel(
        *(mp4_probe.probe(url, parser.get_default_headers(), via_relay=True) for url in urls)
    )
    baseline = measured[0]
    if baseline is None:
        return urls[0]
    side = min(baseline.width, baseline.height)
    candidates = [
        (url, fmt)
        for url, fmt in zip(urls, measured, strict=True)
        if fmt is not None and fmt.width == fmt.height and fmt.width >= side and fmt.fps + 0.1 >= baseline.fps
    ]
    best = max(candidates, key=lambda row: (row[1].width, row[1].fps), default=None)
    return best[0] if best else urls[0]


async def dynamic_cover(parser: BaseParser, cover: str, *, square: bool = False) -> str:
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
    urls = list(
        dict.fromkeys(url for vid, header in candidates if (url := _best_video(data, vid, artwork_only=header)))
    )
    if not urls:
        return ""
    return await _square_url(urls, parser) if square else urls[0]
