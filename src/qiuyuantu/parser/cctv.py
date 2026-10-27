import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlparse

from ..utils import gather_or_cancel
from . import mp4_probe
from .base import BaseParser, FormatInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import number, rank

# 央视网页面里嵌的视频 GUID
_GUID = re.compile(r'var\s+guid\s*=\s*"([^"]+)"')
_CHAPTER = re.compile(r"chapters[0-9]*\Z")
API = "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do"


@dataclass(frozen=True)
class Chapter:
    url: str
    duration: float


def chapters(data: dict, key: str) -> list[Chapter]:
    """只接收完整的一组 MP4 分段；缺少尾段时不能冒充完整节目。"""
    rows = (data.get("video") or {}).get(key)
    if not _CHAPTER.fullmatch(key) or not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise ParseError("parse", "央视网视频分段无效")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ParseError("parse", "央视网视频分段不完整")
        url, duration = row.get("url") or "", number(row.get("duration"))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or not duration:
            raise ParseError("parse", "央视网视频分段不完整")
        result.append(Chapter(parsed._replace(scheme="https").geturl(), duration))
    total = number(data["video"].get("totalLength"))
    if total and abs(sum(part.duration for part in result) - total) > 1:
        raise ParseError("parse", "央视网视频分段时长不完整")
    return result


async def chapter_formats(data: dict) -> list[FormatInfo]:
    keys = [key for key, rows in (data.get("video") or {}).items() if _CHAPTER.fullmatch(key) and rows]
    if len(keys) > 8:
        raise ParseError("parse", "央视网清晰度档位数量异常")
    groups = [chapters(data, key) for key in keys]
    probes = await gather_or_cancel(*(mp4_probe.probe(parts[0].url) for parts in groups))
    formats = []
    for key, parts, fmt in zip(keys, groups, probes, strict=True):
        if fmt:
            formats.append(
                replace(
                    fmt,
                    url="",
                    format_spec=f"cctv:{key}:{fmt.width}x{fmt.height}",
                    filesize=int(fmt.filesize * sum(p.duration for p in parts) / parts[0].duration),
                )
            )
    return sorted(formats, key=rank, reverse=True)


class CCTV(BaseParser):
    """央视网"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        data = await self.video_data(share_url)
        return await self._info(data, share_url)

    async def video_data(self, page_url: str) -> dict:
        parsed = urlparse(page_url)
        if parsed.hostname == "vdn.apps.cntv.cn" and parsed.path == "/api/getHttpVideoInfo.do":
            video_id = (parse_qs(parsed.query).get("pid") or [""])[0]
        else:
            video_id = self._extract_guid_from_html(await self.get_text(page_url))
        return await self._data(video_id)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self._info(await self._data(video_id), f"{API}?pid={video_id}")

    async def _data(self, video_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id):
            raise ParseError("unsupported", "视频 GUID 不能为空")
        data = await self.get_json(f"{API}?pid={video_id}")
        if data.get("status", "") != "001":
            raise ParseError("restricted", "央视网没有提供这条视频的数据")
        if str(data.get("is_protected", "0")) != "0":
            raise ParseError("copyright")
        return data

    @staticmethod
    async def _info(data: dict, page_url: str) -> VideoInfo:
        # manifest 中的 enc / h5e 流可能在 H.264 帧级加扰；MP4 分段不经过这些清单。
        video_url = data.get("hls_url", "")
        formats = await chapter_formats(data)
        if video_url:
            formats.append(FormatInfo(label="HLS", format_spec="cctv:hls"))
        if not video_url and not formats:
            raise ParseError("empty", "央视网没有给播放地址")
        return VideoInfo(
            video_url=video_url,
            video_ext="m3u8",
            page_url=page_url,
            formats=formats,
            cover_url=data.get("image", ""),
            title=data.get("title", ""),
            author=VideoAuthor(name=data.get("play_channel", "")),
            duration=number((data.get("video") or {}).get("totalLength")),
        )

    @staticmethod
    def _extract_guid_from_html(html: str) -> str:
        match = _GUID.search(html)
        if match and match.group(1):
            return match.group(1)
        raise ParseError("parse", "页面中未找到视频 GUID")
