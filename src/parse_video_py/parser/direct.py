"""直接贴进来的图片 / 视频文件地址（各平台 CDN 上的），不用解析，原样给出去走代理下载。

2026-10-10 有人贴了一条 p11-sign.douyinpic.com 的抖音图片地址试了 3 次：以前它被当成普通网页交给
yt-dlp，yt-dlp 去「解析页面」当然不行。光凭文件地址找不回原作品，所以给的就是这一张（多半是网页上
压缩过的版本），结果里提示想要原图就复制作品的分享链接。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import ImgInfo, VideoInfo

# CDN 域名 -> 结果卡片上显示的平台
_CDN_SOURCES = {
    "douyinpic.com": "douyin",
    "douyinvod.com": "douyin",
    "xhscdn.com": "redbook",
    "sinaimg.cn": "weibo",
    "weibocdn.com": "weibo",
    "hdslb.com": "bilibili",
    "bilivideo.com": "bilibili",
    "bilivideo.cn": "bilibili",
    "twimg.com": "twitter",
    "cdninstagram.com": "instagram",
    "yximgs.com": "kuaishou",
    "kwimgs.com": "kuaishou",
    "kwaicdn.com": "kuaishou",
}
# 这些域名上的是视频；其余 CDN 上没有扩展名的地址（小红书、抖音图片常这样）当图片
_VIDEO_HOSTS = ("douyinvod.com", "bilivideo.com", "bilivideo.cn", "video.twimg.com", "weibocdn.com", "kwaicdn.com")
_VIDEO_EXT = re.compile(r"\.(mp4|mov|m4v|webm)$", re.I)
_NOTICE = (
    "这是{kind}文件的地址，下载的就是这一个（多半是网页上压缩过的版本）；"
    "想要原图和整条作品，复制作品的分享链接再来解析。"
)


def _cdn_source(host: str) -> str:
    return next((src for domain, src in _CDN_SOURCES.items() if host == domain or host.endswith("." + domain)), "")


def direct_media(url: str) -> VideoInfo | None:
    """是已知平台 CDN 上的文件地址就直接给出去，不是返回 None。不发请求。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    source = _cdn_source(host)
    if not source:
        return None
    is_video = bool(_VIDEO_EXT.search(parsed.path)) or any(host == h or host.endswith("." + h) for h in _VIDEO_HOSTS)
    if is_video:
        return VideoInfo(video_url=url, cover_url="", title="视频", source=source, notice=_NOTICE.format(kind="视频"))
    return VideoInfo(
        video_url="",
        cover_url=url,
        title="图片",
        images=[ImgInfo(url=url)],
        source=source,
        notice=_NOTICE.format(kind="图片"),
    )
