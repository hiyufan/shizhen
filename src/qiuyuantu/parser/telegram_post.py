"""Telegram 公开频道 / 公开群的单条帖子：t.me/{频道}/{编号}。

不用账号或 Bot，读的是 Telegram 官方的帖子嵌入页（?embed=1，网页上「Embed」给出的那个）。
限制都来自这个页面，解析结果要照实说：

- 图片只有网页预览尺寸，最长边 800 像素；原尺寸只能在 App 里保存。
- 视频是原文件，但超过网页版大小上限的只显示封面和「Media is too big」，拿不到。
- 开了内容保护的频道、被 Telegram 屏蔽的频道，页面里没有媒体或只有一句英文说明。
- 私有频道（t.me/c/…）只有成员能看，匿名打不开。

相册里的图片放 images；只有一个视频时放 video_url，多个视频都作为独立素材放 assets。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from lxml import html

from .base import BaseParser, ImgInfo, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError

_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me", "telegram.dog"}
_CHANNEL = r"[A-Za-z][A-Za-z0-9_]{3,31}"
# /频道/编号、/s/频道/编号（频道预览页）、/群/话题/编号（论坛群的话题消息）
_PUBLIC = re.compile(rf"^/(?:s/)?(?P<channel>{_CHANNEL})/(?:\d+/)?(?P<post>\d+)/?$")
_PRIVATE = re.compile(r"^/c/\d+/(?:\d+/)?\d+/?$")
_BG_URL = re.compile(r"background-image:url\('([^']+)'\)")
_MAX_PAGE = 2_000_000

PHOTO_NOTICE = "Telegram 网页版只提供最长边 800 像素的图片；要原尺寸请在 Telegram 里保存。"
BIG_NOTICE = "部分视频超过 Telegram 网页版的大小上限，需在 Telegram 里保存。"


def is_post_page(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if (parsed.hostname or "").lower() not in _HOSTS:
        return False
    return bool(_PUBLIC.match(parsed.path) or _PRIVATE.match(parsed.path))


def post_ref(url: str) -> tuple[str, str, bool]:
    """(频道, 编号, 是否只要相册里的这一张)；私有频道直接说明。"""
    parsed = urlparse(url)
    if _PRIVATE.match(parsed.path):
        raise ParseError("restricted", "这是私有频道或私有群的消息链接，只有成员能看，无法匿名读取")
    m = _PUBLIC.match(parsed.path)
    if not m:
        raise ParseError("unsupported", "请粘贴公开频道里单条帖子的链接，例如 https://t.me/频道名/123")
    return m.group("channel"), m.group("post"), "single" in parse_qs(parsed.query, keep_blank_values=True)


def _bg(node) -> str:
    m = _BG_URL.search(node.get("style") or "")
    return m.group(1) if m else ""


def _class(name: str) -> str:
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def _videos(message) -> tuple[list[tuple[str, str]], int]:
    """[(视频地址, 封面)]，以及网页版不给的超大视频个数。"""
    found, too_big = [], 0
    for player in message.xpath(
        f".//*[{_class('tgme_widget_message_video_player')} or {_class('tgme_widget_message_roundvideo_player')}]"
    ):
        if "not_supported" in (player.get("class") or "").split():
            too_big += 1
            continue
        src = next(iter(player.xpath(".//video/@src")), "")
        thumbs = player.xpath(
            f".//*[{_class('tgme_widget_message_video_thumb')} or {_class('tgme_widget_message_roundvideo_thumb')}]"
        )
        if src:
            found.append((src, _bg(thumbs[0]) if thumbs else ""))
    return found, too_big


def _no_media_reason(message) -> ParseError:
    if message.xpath(f".//*[{_class('tgme_widget_message_document')}]"):
        return ParseError("restricted", "这条帖子是文件附件，Telegram 网页版不提供下载，请在 Telegram 里保存")
    if message.xpath(f".//*[{_class('tgme_widget_message_text')}]"):
        return ParseError("empty", "这条帖子只有文字")
    return ParseError(
        "restricted", "这条帖子的媒体 Telegram 网页版不显示，频道可能开启了内容保护，只能在 Telegram 里查看"
    )


def _notice(images: list, too_big: int) -> str:
    parts = [PHOTO_NOTICE if images else "", BIG_NOTICE if too_big else ""]
    return "".join(parts)


def to_video_info(page: str, channel: str, post: str) -> VideoInfo:
    tree = html.fromstring(page)
    # 出错的帖子（不存在、频道被屏蔽）没有 data-post，只有一句 tgme_widget_message_error
    messages = tree.xpath(f"//*[{_class('tgme_widget_message')}]")
    if not messages:
        raise ParseError("parse", "嵌入页里没有帖子")
    message = messages[0]
    error = message.xpath(f"string(.//*[{_class('tgme_widget_message_error')}])").strip()
    if error:
        if "not found" in error.lower():
            raise ParseError("deleted")
        raise ParseError("restricted", "Telegram 不在网页上展示这个频道或群，只能在 Telegram 里查看")
    photos = [url for node in message.xpath(f".//a[{_class('tgme_widget_message_photo_wrap')}]") if (url := _bg(node))]
    videos, too_big = _videos(message)
    if not photos and not videos:
        if too_big:
            raise ParseError("restricted", "视频超过 Telegram 网页版的大小上限，只能在 Telegram 里保存")
        raise _no_media_reason(message)
    images = [ImgInfo(url=url) for url in photos]
    text = message.xpath(f"string(.//*[{_class('tgme_widget_message_text')}])").strip()
    owner = message.xpath(f"string(.//*[{_class('tgme_widget_message_owner_name')}])").strip()
    single = videos[0] if len(videos) == 1 else ("", "")
    return VideoInfo(
        title=" ".join(text.split())[:80],
        video_url=single[0],
        cover_url=single[1] or (photos[0] if photos else videos[0][1]),
        images=images,
        assets=[]
        if single[0]
        else [MediaAsset(url=url, cover_url=cover, kind="video", ext="mp4") for url, cover in videos],
        author=VideoAuthor(uid=channel, name=owner or channel),
        source="telegram",
        page_url=f"https://t.me/{channel}/{post}",
        notice=_notice(images, too_big),
    )


class TelegramPost(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        channel, post, single = post_ref(share_url)
        embed = f"https://t.me/{channel}/{post}?embed=1&mode=tme" + ("&single=1" if single else "")
        response = await self.fetch("GET", embed)
        if len(response.content) > _MAX_PAGE:
            raise ParseError("too_large", "帖子页面过大")
        return to_video_info(response.text, channel, post)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(f"https://t.me/{video_id}")
