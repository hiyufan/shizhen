"""用 yt-dlp 的清单解析器读取实际档位；网络请求仍由平台解析器完成。"""

import re
from urllib.parse import quote
from xml.etree import ElementTree

from .base import FormatInfo
from .errors import ParseError
from .quality import number, short_side


def tracks(text: str, url: str, kind: str) -> list[dict]:
    import yt_dlp
    from yt_dlp.extractor.common import InfoExtractor

    if len(text) > 1 << 20 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ParseError("parse", "视频清单超过限制或包含外部声明")
    # 禁用不连续流拆分，否则纯解析方法也可能另外请求子清单。
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "hls_split_discontinuity": False}) as downloader:
        extractor = InfoExtractor(downloader)
        if kind == "hls":
            if not text.lstrip().startswith("#EXTM3U"):
                raise ParseError("parse", "来源没有返回有效的 HLS 清单")
            formats, _ = extractor._parse_m3u8_formats_and_subtitles(text, url, ext="mp4")
        elif kind == "dash":
            document = ElementTree.fromstring(text)
            if document.get("type") == "dynamic":
                raise ParseError("unsupported", "暂不支持下载直播清单")
            formats, _ = extractor._parse_mpd_formats_and_subtitles(document, mpd_url=url)
        else:
            raise ParseError("unsupported", "不支持的视频清单类型")
    if len(formats) > 256:
        raise ParseError("parse", "视频清单档位数量异常")
    return [fmt for fmt in formats if not fmt.get("has_drm") and fmt.get("vcodec") != "none"]


def download_format(track: dict, prefix: str) -> FormatInfo:
    width, height = int(number(track.get("width"))), int(number(track.get("height")))
    fps, bitrate = number(track.get("fps")), int(number(track.get("tbr")) * 1000)
    codec = str(track.get("vcodec") or "")
    return FormatInfo(
        label=(f"{short_side(width, height)}p" if width or height else prefix.rsplit(":", 1)[-1].upper())
        + (str(round(fps)) if fps > 45 else ""),
        format_spec=f"{prefix}:{width}x{height}:{fps}:{bitrate}:{quote(codec, safe='')}",
        width=width,
        height=height,
        fps=fps,
        bitrate=bitrate,
        codec=codec,
        filesize=int(number(track.get("filesize") or track.get("filesize_approx"))),
    )


def selector(fmt: FormatInfo) -> str:
    """精确选择解析时的尺寸和帧率；来源缺档时不能用 /best 偷偷降级。"""
    filters = "".join(f"[{key}={value}]" for key, value in (("width", fmt.width), ("height", fmt.height)) if value)
    if fmt.fps:
        filters += f"[fps>={max(0, fmt.fps - 0.1)}]"
    if fmt.bitrate:
        filters += f"[tbr>={max(0, fmt.bitrate / 1000 - 0.1)}]"
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", fmt.codec):
        filters += f"[vcodec={fmt.codec}]"
    return f"bv*{filters}+ba/b{filters}/bv{filters}"
