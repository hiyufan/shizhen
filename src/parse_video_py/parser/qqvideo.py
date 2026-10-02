import json
import re
from urllib.parse import parse_qs, urlparse

from .base import BaseParser, VideoInfo
from .errors import ParseError

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
        body = await self.get_text(
            f"https://vv.video.qq.com/getinfo?vids={video_id}&platform=101001&otype=json&defn=shd"
        )
        # JSONP：去掉前缀 QZOutputJson= 和结尾的分号
        data = json.loads(body.removeprefix("QZOutputJson=").removesuffix(";"))
        if data.get("em", 0) != 0:
            raise ValueError(f"腾讯视频API返回错误: {data.get('msg', '')} (em: {data.get('em')})")
        videos = (data.get("vl") or {}).get("vi") or []
        if not videos:
            raise ParseError("deleted", "腾讯视频没有返回这条视频，可能已被删除或设为私密")
        vi = videos[0]
        vid = vi.get("vid", "")
        return VideoInfo(
            video_url=_play_url(vi),
            cover_url=f"https://puui.qpic.cn/vpic_cover/{vid}/{vid}_hz.jpg/496",
            title=vi.get("ti", ""),
        )

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
