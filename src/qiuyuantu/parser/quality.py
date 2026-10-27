"""按来源实际返回的媒体信息选择档位，不把文件大小或编码名称当作画质。"""

import math
from urllib.parse import urlparse

from ..utils import gather_or_cancel
from .base import FormatInfo, VideoInfo
from .errors import ParseError


def number(value) -> float:
    """平台数字可能是字符串，缺失和非有限值不能参与档位排序。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0
    return result if math.isfinite(result) and result > 0 else 0


def short_side(width: int, height: int) -> int:
    return min(width, height) if width and height else height or width


def frame_rate(value) -> float:
    if isinstance(value, str) and "/" in value:
        numerator, denominator = value.split("/", 1)
        divisor = number(denominator)
        return number(numerator) / divisor if divisor else 0
    return number(value)


def rank(fmt: FormatInfo) -> tuple[int, float, int]:
    return short_side(fmt.width, fmt.height), fmt.fps, fmt.bitrate


def direct_format(url: str, data: dict, *, label: str = "") -> FormatInfo:
    """只读取实际元数据；标签仅供展示，不据「蓝光 / 原画」猜分辨率。"""
    width, height = int(number(data.get("width"))), int(number(data.get("height")))
    fmt = FormatInfo(
        url=url,
        label=f"{short_side(width, height)}p" if width or height else label,
        width=width,
        height=height,
        fps=frame_rate(data.get("fps") or data.get("frameRate")),
        bitrate=int(number(data.get("bitrate") or data.get("avgBitrate"))),
        filesize=int(number(data.get("size") or data.get("fileSize"))),
        codec=str(data.get("codecs") or data.get("videoCodec") or ""),
    )
    if urlparse(url).path.lower().endswith(".m3u8"):
        fmt.url = ""
        selection = f"[height={height}]" if height else ""
        fmt.format_spec = f"bv*{selection}+ba/b{selection}/bv{selection}"
    return fmt


async def verify_direct_formats(
    formats: list[FormatInfo], headers: dict[str, str] | None = None, *, all_tracks: bool = False
) -> list[FormatInfo]:
    """缺少元数据的直链做有界 MP4 探测；需要核对平台尺寸时可检查全部档位。"""
    from . import mp4_probe

    if len(formats) > 32:
        raise ParseError("parse", "来源返回的清晰度档位数量异常")
    urls = list(
        dict.fromkeys(
            fmt.url
            for fmt in formats
            if urlparse(fmt.url).scheme in {"http", "https"} and (all_tracks or not fmt.height)
        )
    )
    probes = await gather_or_cancel(*(mp4_probe.probe(url, headers) for url in urls))
    verified = {url: probe for url, probe in zip(urls, probes, strict=True) if probe}
    return [verified.get(fmt.url) or fmt for fmt in formats]


def best_video_format(info: VideoInfo) -> FormatInfo | None:
    """下载与兼容预览分开；目录、音频和不同构图的动态封面不互相竞争。"""
    if info.content_kind != "media" or info.source == "applemusic":
        return None
    formats = [f for f in info.formats if f.ext in {"mp4", "webm", "mov", "mkv"} and (f.height or f.format_spec)]
    if info.video_url and info.video_ext != "m3u8":
        formats.insert(
            0,
            FormatInfo(
                url=info.video_url,
                label=f"{short_side(info.width, info.height)}p" if info.width or info.height else "",
                width=info.width,
                height=info.height,
                fps=info.video_fps,
                bitrate=info.video_bitrate,
                codec=info.video_codec,
                ext=_primary_extension(info),
            ),
        )
    # Python 的 max 保留同分时的首项；没有证据更好时保留兼容预览。
    return max(formats, key=rank, default=None)


def _primary_extension(info: VideoInfo) -> str:
    path = urlparse(info.video_url).path.lower()
    suffix = path.rsplit(".", 1)[-1]
    return suffix if suffix in {"webm", "mov", "mkv"} else info.video_ext


def apply_video_formats(info: VideoInfo, formats: list[FormatInfo]) -> VideoInfo:
    """保留所有档位，以最高直接播放档作为预览；清单由服务端下载，不伪装成 MP4。"""
    unique = {f.url or f.format_spec: f for f in formats if f.url or f.format_spec}
    ranked = sorted(unique.values(), key=rank, reverse=True)
    if not ranked:
        return info
    best = next((f for f in ranked if f.url), None)
    info.formats = [f for f in ranked if f is not best]
    if best is None:
        return info
    info.video_url, info.width, info.height = best.url, best.width, best.height
    info.video_fps, info.video_bitrate, info.video_codec = best.fps, best.bitrate, best.codec
    return info
