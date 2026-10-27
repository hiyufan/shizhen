import base64
import json
import re
from urllib.parse import urljoin, urlparse

from parsel import Selector

from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import apply_video_formats, direct_format, verify_direct_formats

_MEDIA_ID = re.compile(r"/(?:video|media)/(?:[0-9]+/)?([0-9]{1,20})(?:/|$)")


def _cut(s: str, start: int, length: int) -> str:
    """去掉 s[start:start+length]，并把后面再出现的同样一段也删掉"""
    piece = s[start : start + length]
    return s[:start] + s[start + length :].replace(piece, "")


def decode_video_url(encoded: str) -> str:
    """美拍页面上的视频地址是混淆过的 base64：前 4 位倒过来是个十六进制数，
    它的十进制各位数字给出两次「从哪儿删多长」，删完剩下的才是真正的 base64。"""
    digits = [int(d) for d in str(int(encoded[:4][::-1], 16))]
    head, tail = digits[:-2], digits[-2:]
    body = _cut(encoded[4:], head[0], head[1])
    body = _cut(body, len(body) - tail[0] - tail[1], tail[1])
    raw = base64.b64decode(body).decode("utf-8")
    url = "https:" + raw if raw.startswith("//") else raw
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("美拍视频地址不是有效的 HTTP 链接")
    return url


def _php_info(html: str, ident: str) -> VideoInfo:
    match = re.search(r"window\.PHPDATA\s*=\s*", html)
    if not match:
        raise ParseError("parse", "美拍页面没有提供作品数据")
    try:
        data, _ = json.JSONDecoder().raw_decode(html, match.end())
    except ValueError as error:
        raise ParseError("parse", "美拍作品数据不完整") from error
    media = data.get("mediaInfo") or {}
    if media.get("id") and str(media["id"]) != ident:
        raise ParseError("parse", "美拍返回的作品编号与链接不一致")
    if not media.get("video"):
        raise ParseError("restricted", "美拍没有提供这条作品的公开视频")
    user = media.get("user") or {}
    return VideoInfo(
        video_url=decode_video_url(media["video"]),
        cover_url=urljoin("https://www.meipai.com/", media["cover_pic"]) if media.get("cover_pic") else "",
        title=re.sub(r"<[^>]*>", "", media.get("caption_origin") or media.get("caption") or "").strip(),
        author=VideoAuthor(
            uid=str(user.get("id") or ""),
            name=user.get("screen_name") or "",
            avatar=urljoin("https://www.meipai.com/", user["avatar"]) if user.get("avatar") else "",
        ),
        page_url=f"https://www.meipai.com/media/{ident}",
    )


def _legacy_info(sel: Selector, encoded: str) -> VideoInfo:
    return VideoInfo(
        video_url=decode_video_url(encoded),
        cover_url=sel.css("#detailVideo img::attr(src)").get(default=""),
        title=sel.css(".detail-cover-title::text").get(default="").strip(),
        author=VideoAuthor(
            uid=sel.css(".detail-name a::attr(href)").get(default="").split("/")[-1],
            name=sel.css(".detail-avatar::attr(alt)").get(default=""),
            avatar=urljoin("https://www.meipai.com/", sel.css(".detail-avatar::attr(src)").get(default="")),
        ),
    )


class MeiPai(BaseParser):
    """美拍"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        target = urlparse(share_url)._replace(scheme="https").geturl()
        html = await self.get_text(target, headers={"User-Agent": self.ua("windows")}, follow_redirects=True)
        sel = Selector(html)
        encoded = sel.css("#shareMediaBtn::attr(data-video)").get(default="")
        if encoded:
            info = _legacy_info(sel, encoded)
        else:
            match = _MEDIA_ID.search(urlparse(share_url).path)
            if not match:
                raise ParseError("unsupported", "美拍链接里没有有效的作品编号")
            if "window.PHPDATA" not in html:
                html = await self.get_text(
                    f"https://www.meipai.com/media/{match[1]}",
                    headers={"User-Agent": self.ua("iOS")},
                    follow_redirects=True,
                )
            info = _php_info(html, match[1])
        info.video_headers = {"Referer": "https://www.meipai.com/"}
        formats = await verify_direct_formats([direct_format(info.video_url, {})], info.video_headers)
        return apply_video_formats(info, formats)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[0-9]{1,20}", video_id):
            raise ParseError("unsupported", "美拍作品编号无效")
        return await self.parse_share_url(f"https://www.meipai.com/video/{video_id}")
