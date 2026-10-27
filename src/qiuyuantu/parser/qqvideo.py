import json
import re
from urllib.parse import parse_qs, urlparse

import httpx

from ..utils import gather_or_cancel
from .base import BaseParser, FormatInfo, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, number

# 腾讯视频页面路径里的视频 ID
_VID_IN_PATH = re.compile(r"/x/(?:page|cover)/(?:[^/]+/)?(\w+)\.html")


def _play_url(vi: dict) -> str:
    """CDN 前缀 + 文件名 + vkey 拼成播放地址"""
    hosts = (vi.get("ul") or {}).get("ui") or []
    base, fn, fvkey = (hosts[0].get("url", "") if hosts else ""), vi.get("fn", ""), vi.get("fvkey", "")
    if not (base and fn and fvkey):
        raise ParseError("parse", "腾讯视频返回的地址信息不完整")
    return f"{base}{fn}?vkey={fvkey}"


class QQVideo(BaseParser):
    """腾讯视频"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(self._extract_vid(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not video_id:
            raise ParseError("unsupported", "视频 ID 不能为空")
        data = await self._info(video_id, "shd")
        vi = _video(data)
        formats = [_format(data)]
        qualities = _other_qualities(data, vi)
        if qualities:
            extra = await gather_or_cancel(*(self._optional_format(video_id, q) for q in qualities))
            formats.extend(f for f in extra if f)
        vid = vi.get("vid", "")
        info = VideoInfo(
            video_url="",
            cover_url=f"https://puui.qpic.cn/vpic_cover/{vid}/{vid}_hz.jpg/496",
            title=vi.get("ti", ""),
            duration=number(vi.get("td")),
        )
        return apply_video_formats(info, formats)

    async def _info(self, video_id: str, quality: str) -> dict:
        body = await self.get_text(
            f"https://vv.video.qq.com/getinfo?vids={video_id}&platform=101001&otype=json&defn={quality}"
        )
        # JSONP：去掉前缀 QZOutputJson= 和结尾的分号
        data = json.loads(body.removeprefix("QZOutputJson=").removesuffix(";"))
        if data.get("em", 0) != 0:
            # 「该内容暂不可观看」（em 61）是会员 / 版权内容，以前被归成「页面结构变了」
            raise ParseError("restricted", f"腾讯视频：{data.get('msg', '')}（em {data.get('em')}）")
        return data

    async def _optional_format(self, video_id: str, quality: str) -> FormatInfo | None:
        try:
            return _format(await self._info(video_id, quality))
        except (httpx.HTTPError, ParseError, ValueError):
            return None

    @staticmethod
    def _extract_vid(raw_url: str) -> str:
        """移动端 m.v.qq.com/x/m/play?vid=<vid>；电脑端 v.qq.com/x/page/<vid>.html、/x/cover/<cid>/<vid>.html"""
        parsed = urlparse(raw_url)
        host = parsed.hostname or ""
        if "m.v.qq.com" in host:
            vid = parse_qs(parsed.query).get("vid", [""])[0]
        elif "v.qq.com" in host:
            match = _VID_IN_PATH.search(parsed.path)
            vid = match.group(1) if match else ""
        else:
            vid = ""
        if not vid:
            raise ParseError("unsupported", "链接里没有腾讯视频的视频 ID")
        return vid


def _video(data: dict) -> dict:
    videos = (data.get("vl") or {}).get("vi") or []
    if not videos:
        raise ParseError("deleted", "腾讯视频没有返回这条视频，可能已被删除或设为私密")
    vi = videos[0]
    if number(vi.get("drm")):
        raise ParseError("restricted", "腾讯视频返回的是加密视频")
    return vi


def _format(data: dict) -> FormatInfo:
    vi = _video(data)
    quality = next((f for f in (data.get("fl") or {}).get("fi") or [] if f.get("br") == vi.get("br")), {})
    if number(quality.get("drm")):
        raise ParseError("restricted", "腾讯视频返回的是加密视频")
    width, height = int(number(vi.get("vw"))), int(number(vi.get("vh")))
    segmented = number((vi.get("cl") or {}).get("fc")) > 1
    selection = f"[height={height}]" if height else ""
    return FormatInfo(
        url="" if segmented else _play_url(vi),
        format_spec=f"bv*{selection}+ba/b{selection}" if segmented else "",
        label=f"{min(width, height)}p" if width and height else quality.get("sname") or "",
        width=width,
        height=height,
        fps=number(quality.get("vfps")),
        bitrate=int(number(quality.get("bandwidth"))),
        filesize=int(number(vi.get("fs"))),
    )


def _other_qualities(data: dict, vi: dict) -> list[str]:
    return list(
        dict.fromkeys(
            f["name"]
            for f in (data.get("fl") or {}).get("fi") or []
            if re.fullmatch(r"[A-Za-z0-9_]{1,24}", f.get("name") or "")
            and not number(f.get("drm"))
            and not number(f.get("lmt"))
            and f.get("br") != vi.get("br")
        )
    )
