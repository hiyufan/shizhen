import re
import time
from urllib.parse import urlparse, urlunparse

from .base import BaseParser, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, direct_format, verify_direct_formats


def _video_url(value: str, timestamp: str, video_id: str) -> str:
    parsed = urlparse("https:" + value if value.startswith("//") else value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ParseError("parse", "梨视频返回的视频地址无效")
    # 只替换文件名中的占位时间，不改目录日期或签名参数。
    parent, separator, name = parsed.path.rpartition("/")
    if timestamp and name.startswith(timestamp + "-"):
        name = f"cont-{video_id}" + name[len(timestamp) :]
    if (match := re.search(r"cont-([0-9]+)-", name)) and match[1] != video_id:
        raise ParseError("parse", "梨视频返回的作品编号与链接不一致")
    return urlunparse(parsed._replace(path=parent + separator + name))


class LiShiPin(BaseParser):
    """梨视频"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        # 首页和列表里是 /video_<id>，分享出来是 /detail_<id>，接口地址里是 contId=<id>
        m = re.search(r"(?:/video_|/detail_|contId=)(\d+)", share_url)
        video_id = m.group(1) if m else ""
        if not video_id:
            raise ParseError("unsupported", "链接里没有梨视频的作品 ID")
        return await self.parse_video_id(video_id)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[0-9]{1,20}", video_id):
            raise ParseError("unsupported", "梨视频链接里没有有效的作品编号")
        headers = {"Referer": f"https://www.pearvideo.com/detail_{video_id}", "User-Agent": self.ua("windows")}
        body = await self.get_json(
            f"https://www.pearvideo.com/videoStatus.jsp?contId={video_id}&mrd={int(time.time())}",
            headers=headers,
        )
        if str(body.get("resultCode", "1")) != "1":
            raise ParseError("restricted", "梨视频没有返回这条作品的播放信息")
        info = body.get("videoInfo") or {}
        if str(info.get("playSta", "1")) != "1":
            raise ParseError("restricted", "梨视频当前未开放这条作品的播放")
        videos = info.get("videos") or {}
        timestamp = str(body.get("systemTime") or "")
        # 这些字段是点播 MP4；直播的 hdflvUrl / sdflvUrl 不混作同一作品的原文件。
        formats = [
            direct_format(_video_url(videos[key], timestamp, video_id), {})
            for key in ("srcUrl", "hdUrl", "sdUrl", "ldUrl")
            if videos.get(key)
        ]
        if not formats:
            raise ParseError("empty", "梨视频没有返回可下载的视频地址")
        result = VideoInfo(video_url="", cover_url=info.get("video_image") or "", video_headers=headers)
        return apply_video_formats(result, await verify_direct_formats(formats, headers, all_tracks=True))
