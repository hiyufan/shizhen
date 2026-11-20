"""Instagram 历史失败回归：图片帖的不同 yt-dlp 错误都能尝试公开页面，兜底不覆盖原错。"""

import asyncio

import httpx
import pytest

from qiuyuantu.parser import instagram
from qiuyuantu.parser.base import ImgInfo, VideoInfo
from qiuyuantu.parser.errors import classify
from qiuyuantu.parser.ytdlp import YtDlp

POST = "https://www.instagram.com/p/example/?img_index=2"


@pytest.fixture
def image_info():
    return VideoInfo(
        video_url="",
        cover_url="https://cdn.example/photo.jpg",
        images=[ImgInfo(url="https://cdn.example/photo.jpg")],
        source="instagram",
    )


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [Instagram] example: There is no video in this post",
        "ERROR: [Instagram] example: No video formats found!",
        "ERROR: [Instagram] example: Instagram sent an empty media response",
        "ERROR: [Instagram] example: HTTP Error 429: Too Many Requests",
    ],
)
async def test_public_image_post_recovers_from_ytdlp_errors(monkeypatch, image_info, message):
    def extract(_url):
        raise RuntimeError(message)

    async def image_post(url):
        assert url == POST
        return image_info

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    monkeypatch.setattr(instagram, "image_post", image_post)
    assert await YtDlp().parse_share_url(POST) is image_info


@pytest.mark.parametrize("fallback_error", [None, httpx.ReadTimeout("page timed out")])
async def test_failed_page_fallback_keeps_original_error(monkeypatch, fallback_error):
    original = RuntimeError("This content isn't available to everyone")

    def extract(_url):
        raise original

    async def image_post(_url):
        if fallback_error:
            raise fallback_error

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    monkeypatch.setattr(instagram, "image_post", image_post)
    with pytest.raises(RuntimeError) as exc:
        await YtDlp().parse_share_url(POST)
    assert exc.value is original
    assert classify(exc.value).reason == "restricted"


async def test_page_fallback_can_still_be_cancelled(monkeypatch):
    def extract(_url):
        raise RuntimeError("There is no video in this post")

    async def image_post(_url):
        raise asyncio.CancelledError

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    monkeypatch.setattr(instagram, "image_post", image_post)
    with pytest.raises(asyncio.CancelledError):
        await YtDlp().parse_share_url(POST)


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=example", "https://www.instagram.com/reel/example/"])
async def test_other_platforms_and_reels_do_not_fetch_image_pages(monkeypatch, url):
    def extract(_url):
        raise RuntimeError("There is no video in this post")

    called = []

    async def image_post(_url):
        called.append(_url)

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    monkeypatch.setattr(instagram, "image_post", image_post)
    with pytest.raises(RuntimeError):
        await YtDlp().parse_share_url(url)
    assert called == []


def test_image_only_error_is_not_reported_as_a_changed_page():
    assert classify(RuntimeError("There is no video in this post")).reason == "empty"


def _media(kind, name):
    item = {"media_type": kind, "image_versions2": {"candidates": [{"url": f"https://cdn.example/{name}.jpg"}]}}
    if kind == 2:
        item["video_versions"] = [{"url": f"https://cdn.example/{name}.mp4", "width": 720, "height": 1280}]
    return item


def test_mixed_carousel_keeps_every_image_and_video():
    post = {"carousel_media": [_media(2, "v1"), _media(1, "p1"), _media(2, "v2"), _media(1, "p2")]}
    info = instagram.to_video_info(post, "example")
    assert [i.url for i in info.images] == ["https://cdn.example/p1.jpg", "https://cdn.example/p2.jpg"]
    # 多个视频不能只剩第一个：全部列为素材，各带自己的封面
    assert not info.video_url
    assert [(a.url, a.cover_url, a.kind) for a in info.assets] == [
        ("https://cdn.example/v1.mp4", "https://cdn.example/v1.jpg", "video"),
        ("https://cdn.example/v2.mp4", "https://cdn.example/v2.jpg", "video"),
    ]


def test_single_video_with_images_stays_the_main_video():
    info = instagram.to_video_info({"carousel_media": [_media(1, "p1"), _media(2, "v1")]}, "example")
    assert info.video_url == "https://cdn.example/v1.mp4" and not info.assets
    assert [i.url for i in info.images] == ["https://cdn.example/p1.jpg"]


@pytest.mark.parametrize("count", [1, 3])
async def test_video_only_carousel_uses_the_whole_post_page(monkeypatch, image_info, count):
    called = []

    def extract(_url):
        info = {"id": "first", "url": "https://cdn.example/v1.mp4"}
        return {**info, "_entry_count": count} if count > 1 else info

    async def image_post(url):
        called.append(url)
        return image_info

    async def usable(self, info, _url):
        return VideoInfo(video_url=info["url"], cover_url="")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    monkeypatch.setattr(YtDlp, "_usable_info", usable)
    monkeypatch.setattr(instagram, "image_post", image_post)
    info = await YtDlp().parse_share_url(POST)
    # yt-dlp 只留第一条；多条时改用页面里的整条帖子，单条视频照常用 yt-dlp 的结果
    assert (info is image_info) == (count > 1) and called == ([POST] if count > 1 else [])


async def test_whole_post_page_failure_falls_back_to_ytdlp_result(monkeypatch):
    async def image_post(_url):
        raise httpx.ReadTimeout("page timed out")

    async def usable(self, info, _url):
        return VideoInfo(video_url=info["url"], cover_url="")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(lambda _url: {"url": "u", "_entry_count": 2}))
    monkeypatch.setattr(YtDlp, "_usable_info", usable)
    monkeypatch.setattr(instagram, "image_post", image_post)
    assert (await YtDlp().parse_share_url(POST)).video_url == "u"
