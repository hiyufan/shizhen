"""微信公众号文章：mp.weixin.qq.com/s/…，以及 ?__biz=&mid=&idx=&sn= 的老格式。

文章页里 window.cgiDataNew 是整篇文章的数据（JS 字面量，见 js_literal），按 item_show_type 分三种：

- 8 图片消息：picture_page_info_list 每项的 cdn_url 就是原图（地址末段 /0）。
- 5 视频消息：video_page_info.mp_video_trans_info 是各档 MP4 直链。
- 其它（0 普通图文）：正文 content_noencode 里 <img data-src>，末段 /640 换成 /0 是原图；
  内嵌视频 <iframe data-mpvid> 对应 video_page_infos 里同 video_id 的档位。
  嵌入的视频号、音频不在这份数据里，结果里说明一句。

请求用电脑浏览器 UA：带微信 UA 时只给「未知错误」页。从浏览器地址栏复制的老格式链接参数不全，
请求前整理成微信「复制链接」给出的标准形式（见 standard_url）。仍被转去验证页
（wappoc_appmsgcaptcha，页面同样显示「未知错误」）时重试一次，再不行就请用户从微信里复制链接。
图片 CDN 见到别的站点的 Referer 会换成「未经允许不可引用」的占位图，所以下载时不能带 Referer
（convert.net 的 Referer 表里故意没有 qpic.cn）。
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlencode, urlparse

from .base import BaseParser, FormatInfo, ImgInfo, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError
from .js_literal import parse_at
from .quality import apply_video_formats, direct_format, rank

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_DATA = re.compile(r"window\.cgiDataNew\s*=\s*")
_IMG = re.compile(r"<img\b[^>]*?\bdata-src=\"([^\"]+)\"")
_MPVID = re.compile(r"\bdata-mpvid=\"([^\"]+)\"")
_EMBEDS = ("channels_iframe", "mp-common-mpaudio", "mpvoice")
_ERROR_TITLE = re.compile(r'class="weui-msg__title[^"]*"[^>]*>\s*([^<]+?)\s*<', re.S)
_PICTURE, _VIDEO = 8, 5
_CAPTCHA = "appmsgcaptcha"

CAPTCHA_HINT = "公众号要求先做人机验证，请在微信里打开文章，点右上角「复制链接」后粘贴"
EMBED_NOTICE = "文章里嵌入的视频号或音频不在文章数据里，需要的话请单独分享它们的链接。"


def original_image(url: str) -> str:
    """mmbiz.qpic.cn/…/640?wx_fmt=jpeg → https://mmbiz.qpic.cn/…/0?wx_fmt=jpeg（原尺寸，去掉统计参数）。"""
    url = html.unescape(url or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (host.endswith(".qpic.cn") or host.endswith(".qlogo.cn")):
        return url
    path = re.sub(r"/\d+$", "/0", parsed.path)
    fmt = parse_qs(parsed.query).get("wx_fmt")
    return parsed._replace(scheme="https", path=path, query=urlencode({"wx_fmt": fmt[0]}) if fmt else "").geturl()


def page_data(page: str) -> dict | None:
    m = _DATA.search(page)
    if not m:
        return None
    try:
        data = parse_at(page, m.end())
    except ValueError as exc:
        raise ParseError("parse", f"文章数据读不出来：{exc}") from exc
    return data if isinstance(data, dict) else None


def page_error(page: str, final_url: str = "") -> ParseError:
    """没有文章数据的页面：删除、违规、链接不完整，或者人机验证。"""
    m = _ERROR_TITLE.search(page)
    message = " ".join(html.unescape(m.group(1)).split()) if m else ""
    if _CAPTCHA in final_url or ("未知错误" in page and not message):
        return ParseError("restricted", CAPTCHA_HINT)
    if any(word in message for word in ("删除", "过期", "参数错误", "不存在")):
        return ParseError("deleted", message)
    if message:
        return ParseError("restricted", message)
    return ParseError("parse", "页面里没有文章数据")


def _formats(video: dict) -> list[FormatInfo]:
    found = []
    for track in video.get("mp_video_trans_info") or []:
        url = html.unescape(str(track.get("url") or "")).replace("http://", "https://", 1)
        if url:
            found.append(
                direct_format(
                    url, {"width": track.get("width"), "height": track.get("height"), "size": track.get("filesize")}
                )
            )
    return found


def _article_media(data: dict) -> tuple[list[str], list[dict]]:
    content = str(data.get("content_noencode") or "")
    images = [original_image(url) for url in dict.fromkeys(_IMG.findall(content))]
    by_id = {video.get("video_id"): video for video in data.get("video_page_infos") or [] if isinstance(video, dict)}
    videos = [by_id[vid] for vid in dict.fromkeys(_MPVID.findall(content)) if vid in by_id]
    return images, videos


def _media(data: dict) -> tuple[list[str], list[dict]]:
    kind = data.get("item_show_type")
    if kind == _PICTURE:
        pictures = data.get("picture_page_info_list") or []
        return [original_image(p["cdn_url"]) for p in pictures if isinstance(p, dict) and p.get("cdn_url")], []
    if kind == _VIDEO:
        return [], [data.get("video_page_info") or {}]
    return _article_media(data)


def _attach_videos(info: VideoInfo, videos: list[dict]) -> None:
    """一个视频放视频区（带全部清晰度）；多个视频各取最高档，作为独立素材。"""
    tracks = [(video, fmts) for video in videos if (fmts := _formats(video))]
    if len(tracks) == 1:
        apply_video_formats(info, tracks[0][1])
        info.cover_url = original_image(tracks[0][0].get("cover_url") or "") or info.cover_url
        return
    info.assets = [
        MediaAsset(
            url=max(fmts, key=rank).url, cover_url=original_image(video.get("cover_url") or ""), kind="video", ext="mp4"
        )
        for video, fmts in tracks
    ]


def to_video_info(data: dict, share_url: str) -> VideoInfo:
    images, videos = _media(data)
    content = str(data.get("content_noencode") or "")
    info = VideoInfo(
        title=" ".join(html.unescape(str(data.get("title") or "")).split())[:80],
        video_url="",
        cover_url=original_image(str(data.get("cdn_url") or "")),
        images=[ImgInfo(url=url) for url in images],
        author=VideoAuthor(
            uid=str(data.get("user_name") or ""),
            name=html.unescape(str(data.get("nick_name") or "")),
            avatar=original_image(str(data.get("round_head_img") or "")),
        ),
        source="wechat-mp",
        page_url=str(data.get("link") or share_url),
        notice=EMBED_NOTICE if any(mark in content for mark in _EMBEDS) else "",
    )
    _attach_videos(info, videos)
    if not (info.images or info.video_url or info.assets):
        raise ParseError("empty", info.notice or "这篇文章没有图片或视频")
    return info


_ARTICLE_KEYS = ("__biz", "mid", "idx", "sn")


def standard_url(url: str) -> str:
    """老格式 /s?__biz=&mid=&idx=&sn= 只留定位文章的参数，补齐 chksm；短链和其它地址原样返回。"""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.path.rstrip("/") != "/s" or not all(query.get(key) for key in _ARTICLE_KEYS):
        return url
    params = {key: query[key][0] for key in _ARTICLE_KEYS}
    params["chksm"] = (query.get("chksm") or ["1"])[0]
    return "https://mp.weixin.qq.com/s?" + urlencode(params)


def _challenged(response) -> bool:
    return _CAPTCHA in str(response.url) or ("未知错误" in response.text and not _DATA.search(response.text))


class WeChatMP(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        url = standard_url(share_url)
        response = await self.fetch("GET", url, headers=_HEADERS, follow_redirects=True)
        if _challenged(response):
            response = await self.fetch("GET", url, headers=_HEADERS, follow_redirects=True)
        data = page_data(response.text)
        if data is None:
            raise page_error(response.text, str(response.url))
        return to_video_info(data, share_url)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(f"https://mp.weixin.qq.com/s/{video_id}")
