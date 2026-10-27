import json
import re

from parsel import Selector

from .base import BaseParser, FormatInfo, VideoAuthor, VideoInfo, json_in_html
from .quality import direct_format, number, rank


def _current_formats(video: dict) -> list[FormatInfo]:
    current = video.get("currentVideoInfo") or {}
    formats = []
    for key in ("ksPlayJson", "ksPlayJsonHevc"):
        if not current.get(key):
            continue
        play = json.loads(current[key])
        for group in play.get("adaptationSet") or []:
            for row in group.get("representation") or []:
                if not row.get("url") or not row.get("height"):
                    continue
                fmt = direct_format("", row, label=row.get("qualityLabel") or "")
                fps = "[fps>45]" if fmt.fps > 45 else ""
                h = fmt.height
                fmt.format_spec = f"bv[height={h}]{fps}+ba/b[height={h}]{fps}/bv[height={h}]{fps}"
                fmt.bitrate *= 1000  # ksPlayJson 的 avgBitrate 使用 kbit/s。
                if fmt.fps > 45:
                    fmt.label += str(round(fmt.fps))
                formats.append(fmt)
    best: dict[str, FormatInfo] = {}
    for fmt in sorted(formats, key=rank, reverse=True):
        best.setdefault(fmt.format_spec, fmt)
    return list(best.values())


class AcFun(BaseParser):
    """A站：视频地址是 m3u8"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        html = await self.get_text(
            share_url,
            headers={"User-Agent": self.ua("windows"), "Referer": "https://www.acfun.cn/"},
            follow_redirects=True,
        )
        if match := re.search(r"window\.videoInfo\s*=\s*", html):
            video, _ = json.JSONDecoder().raw_decode(html[match.end() :])
            return self._current_info(video, share_url)
        video = json_in_html(html, r"var videoInfo =\s(.*?);", "视频信息")
        play = json_in_html(html, r"var playInfo =\s(.*?);", "播放信息")
        sel = Selector(html)
        return VideoInfo(
            video_url=play["streams"][0]["playUrls"][0],
            video_ext="m3u8",
            formats=[FormatInfo(label="下载视频", format_spec="bv*+ba/b", ext="mp4")],
            cover_url=video["cover"],
            title=video["title"],
            author=VideoAuthor(
                uid=sel.css("div.up-info > a.info-item1::attr(href)").get(default="").replace("/upPage/", ""),
                name=sel.css("div.up-info span.up-name::text").get(default=""),
                avatar=sel.css("div.up-info span.up-avatar > img::attr(src)").get(default=""),
            ),
        )

    @staticmethod
    def _current_info(video: dict, page: str) -> VideoInfo:
        user = video.get("user") or {}
        return VideoInfo(
            video_url="",
            cover_url=video.get("coverUrl") or "",
            title=video.get("title") or "",
            formats=_current_formats(video),
            page_url=page,
            duration=number((video.get("currentVideoInfo") or {}).get("durationMillis")) / 1000,
            author=VideoAuthor(uid=str(user.get("id") or ""), name=user.get("name") or ""),
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        # acid，格式：ac36935385
        return await self.parse_share_url(f"https://www.acfun.cn/v/{video_id}")
