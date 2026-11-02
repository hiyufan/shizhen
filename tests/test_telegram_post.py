"""Telegram 公开频道帖子：链接识别、嵌入页里的图片 / 视频 / 各种拿不到的情况。页面是按真实结构缩写的假页面。"""

import asyncio

import httpx
import pytest

from qiuyuantu.parser import VideoSource, detect_source, telegram_post
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.telegram import Telegram


def photo(url):
    return f"""<a class="tgme_widget_message_photo_wrap grouped_media_wrap blured js-message_photo"
        style="width:453px;background-image:url('{url}')" href="https://t.me/chan/1?single"></a>"""


def video(src, thumb="https://cdn4.telesco.pe/file/thumb.jpg", big=False):
    cls = (
        "tgme_widget_message_video_player not_supported js-message_video_player"
        if big
        else ("tgme_widget_message_video_player blured js-message_video_player")
    )
    tag = "" if big else f'<video src="{src}" class="tgme_widget_message_video js-message_video"></video>'
    return f"""<a class="{cls}" href="https://t.me/chan/1">
        <i class="tgme_widget_message_video_thumb" style="background-image:url('{thumb}')"></i>
        <div class="tgme_widget_message_video_wrap">{tag}
        <div class="tgme_widget_message_video blured js-message_video_blured">{tag}</div></div></a>"""


def page(body="", text="", error=""):
    head = 'data-post="chan/1"' if not error else ""
    error_html = f'<div class="tgme_widget_message_error">{error}</div>' if error else ""
    text_html = f'<div class="tgme_widget_message_text js-message_text">{text}</div>' if text else ""
    return f"""<html><body><div class="tgme_widget_message text_not_supported_wrap js-widget_message" {head}>
        <div class="tgme_widget_message_bubble">
        <a class="tgme_widget_message_owner_name" href="https://t.me/chan"><span>频道名</span></a>
        {error_html}{body}{text_html}
        <div class="message_media_not_supported_wrap"><div class="message_media_not_supported_label">
        Please open Telegram to view this post</div></div>
        <div class="tgme_widget_message_link_preview"><i class="link_preview_image"
        style="background-image:url('https://cdn4.telesco.pe/file/preview.jpg')"></i></div>
        </div></div></body></html>"""


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/chan_name/123",
        "https://t.me/s/chan_name/123",
        "https://telegram.me/chan_name/123?single",
        "https://t.me/forum_group/77/123",
        "https://t.me/c/1234567/89",
    ],
)
def test_post_links_are_routed_to_telegram(url):
    assert telegram_post.is_post_page(url)
    assert detect_source(url) == VideoSource.Telegram


@pytest.mark.parametrize(
    "url", ["https://t.me/addstickers/HotCherry", "https://t.me/chan_name", "https://t.me/+AbCdEf", "https://x.me/a/1"]
)
def test_other_links_are_not_posts(url):
    assert not telegram_post.is_post_page(url)


def test_album_photos_and_single_video():
    body = (
        photo("https://cdn4.telesco.pe/file/a.jpg")
        + photo("https://cdn4.telesco.pe/file/b.jpg")
        + video("https://v/1.mp4")
    )
    info = telegram_post.to_video_info(page(body, text="  第一行<br>第二行 "), "chan", "1")
    assert [i.url for i in info.images] == ["https://cdn4.telesco.pe/file/a.jpg", "https://cdn4.telesco.pe/file/b.jpg"]
    # 视频外层还有一份模糊背景用的同一个 <video>，只算一个
    assert (info.video_url, info.cover_url, info.assets) == (
        "https://v/1.mp4",
        "https://cdn4.telesco.pe/file/thumb.jpg",
        [],
    )
    assert (info.title, info.author.name, info.author.uid) == ("第一行第二行", "频道名", "chan")
    assert (info.source, info.page_url) == ("telegram", "https://t.me/chan/1")
    assert info.notice == telegram_post.PHOTO_NOTICE


def test_several_videos_become_assets_and_oversized_ones_are_mentioned():
    body = (
        video("https://v/1.mp4", "https://t/1.jpg") + video("https://v/2.mp4", "https://t/2.jpg") + video("", big=True)
    )
    info = telegram_post.to_video_info(page(body), "chan", "1")
    assert info.video_url == "" and info.images == []
    assert [(a.url, a.cover_url, a.kind) for a in info.assets] == [
        ("https://v/1.mp4", "https://t/1.jpg", "video"),
        ("https://v/2.mp4", "https://t/2.jpg", "video"),
    ]
    assert info.cover_url == "https://t/1.jpg"
    assert info.notice == telegram_post.BIG_NOTICE


@pytest.mark.parametrize(
    ("html", "reason", "detail"),
    [
        (page(error="Post not found"), "deleted", ""),
        (page(error="This channel can’t be displayed because it was used to spread …"), "restricted", "不在网页上展示"),
        (page(video("", big=True)), "restricted", "大小上限"),
        (
            page(
                '<div class="tgme_widget_message_document_wrap"><div class="tgme_widget_message_document"></div></div>'
            ),
            "restricted",
            "文件附件",
        ),
        (page(text="只有一段话"), "empty", "只有文字"),
        (page(), "restricted", "内容保护"),
        ("<html><body>改版了</body></html>", "parse", ""),
    ],
)
def test_posts_without_downloadable_media_say_why(html, reason, detail):
    with pytest.raises(ParseError) as exc:
        telegram_post.to_video_info(html, "chan", "1")
    assert exc.value.reason == reason and detail in exc.value.detail


def test_private_channel_link_is_explained_without_network():
    with pytest.raises(ParseError, match="私有频道") as exc:
        asyncio.run(Telegram().parse_share_url("https://t.me/c/1234567/89"))
    assert exc.value.reason == "restricted"


@pytest.mark.parametrize(
    ("url", "embed"),
    [
        ("https://t.me/s/chan_name/123", "https://t.me/chan_name/123?embed=1&mode=tme"),
        ("https://t.me/forum_group/77/123", "https://t.me/forum_group/123?embed=1&mode=tme"),
        ("https://t.me/chan_name/123?single", "https://t.me/chan_name/123?embed=1&mode=tme&single=1"),
    ],
)
def test_post_is_read_from_official_embed_page(monkeypatch, url, embed):
    seen = []

    async def fetch(self, method, target, **kw):
        seen.append(target)
        return httpx.Response(200, text=page(photo("https://cdn4.telesco.pe/file/a.jpg")))

    monkeypatch.setattr(telegram_post.TelegramPost, "fetch", fetch)
    info = asyncio.run(Telegram().parse_share_url(url))
    assert seen == [embed] and len(info.images) == 1
