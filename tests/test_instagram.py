"""Instagram 历史失败回归：图片帖的不同 yt-dlp 错误都能尝试公开页面，兜底不覆盖原错。"""

import asyncio

import httpx
import pytest

from parse_video_py.parser import instagram
from parse_video_py.parser.base import ImgInfo, VideoInfo
from parse_video_py.parser.errors import classify
from parse_video_py.parser.ytdlp import YtDlp

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
