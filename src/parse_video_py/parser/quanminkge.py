import re

from ..utils import get_val_from_url_by_query_key
from .base import BaseParser, VideoAuthor, VideoInfo, json_in_html
from .errors import ParseError

# 昵称里的表情是 [em]e400846[/em] 这种代码
_EMOJI_CODE = re.compile(r"\[em\][^\[]*\[/em\]")


class QuanMinKGe(BaseParser):
    """全民K歌"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "s"))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        html = await self.get_text(
            f"https://kg.qq.com/node/play?s={video_id}", headers={"User-Agent": self.ua("windows")}
        )
        detail = json_in_html(html, r"window.__DATA__ = (.*?); </script>", "作品数据")["detail"]
        # 大多是纯音频的翻唱：没有 playurl_video，只有 playurl（音频）。两个都空的是设了私密 / 下架了；
        # 海外 IP 拿到的也全是空的，所以全民K歌在 CN_SOURCES 里走国内中继（2026-10-14 实测）
        video_url, audio_url = detail.get("playurl_video") or "", detail.get("playurl") or ""
        if not video_url and not audio_url:
            raise ParseError("restricted", "全民K歌没给这首作品的播放地址，可能设了私密或者下架了")
        song = detail.get("song_name") or ""
        content = detail.get("content") or ""
        return VideoInfo(
            video_url=video_url,
            music_url="" if video_url else audio_url,
            cover_url=detail.get("cover") or "",
            # 纯音频作品的 content 多是「我唱了一首歌，快来听听吧。」，歌名更有用
            title=song if not video_url and song else content or song,
            author=VideoAuthor(
                uid=detail.get("uid") or "",
                name=_EMOJI_CODE.sub("", detail.get("nick") or ""),
                avatar=detail.get("avatar") or "",
            ),
        )
