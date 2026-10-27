"""好看 / 度小视的实际多档信息；不以数组位置或「4K」标签推断尺寸。"""

import re

from .base import FormatInfo
from .quality import direct_format, number, verify_direct_formats

_DIMENSIONS = re.compile(r"([0-9]{1,5})\$\$([0-9]{1,5})\Z")


def _format(row: dict, details: dict) -> FormatInfo:
    size = _DIMENSIONS.fullmatch(str(row.get("vodVideoHW") or ""))
    # 接口字段名 HW 表示高度在前；videoInfoExt 的大小与码率是字节和 bit/s。
    metadata = row | {
        "height": int(size[1]) if size else row.get("height"),
        "width": int(size[2]) if size else row.get("width"),
        "size": number(details.get("vodVideoSize")) or number(row.get("fileSize")),
        "bitrate": number(details.get("vodVideoBps")) or number(row.get("bitrate")),
    }
    return direct_format(row["url"], metadata, label=str(row.get("title") or ""))


async def video_formats(meta: dict, referer: str) -> list[FormatInfo]:
    details = meta.get("videoInfoExt") or {}
    formats = [
        _format(row, details.get("default" if row.get("key") == "sd" else row.get("key")) or {})
        for row in meta.get("clarityUrl") or []
        if isinstance(row, dict) and row.get("url")
    ]
    if not formats:
        url = meta.get("playurl") or meta.get("html5_playurl") or ""
        formats = [direct_format(url, {})] if url else []
    return await verify_direct_formats(formats, {"Referer": referer}, all_tracks=True)
