"""TikTok 图文：yt-dlp 报 Unsupported URL 时从 /video/{id} 页面的内嵌数据取原图；短链先跟到落地页。不碰网络。"""

import json

import httpx
import pytest

from qiuyuantu.parser import VideoSource, detect_source, tiktok
from qiuyuantu.parser.ytdlp import YtDlp

POST_ID = "7701605180156005652"
PHOTO = f"https://www.tiktok.com/@sam/photo/{POST_ID}"
IMAGE = "https://p16-common-sign.tiktokcdn-us.com/photomode/1.jpeg?x-signature=a"


def page(item_id=POST_ID, images=True):
    item = {
        "id": item_id,
        "desc": "  三张图 #tag ",
        "author": {"id": "72", "uniqueId": "sam", "nickname": "Sam", "avatarThumb": "https://p16/a.jpeg"},
        "music": {"playUrl": "https://v19.tiktokcdn-us.com/m.mp4"},
        "imagePost": {
            "images": [
                {"imageURL": {"urlList": [IMAGE, "https://p19/1.jpeg"]}},
                {"imageURL": {"urlList": ["https://p16/2.jpeg"]}},
                {"imageURL": {"urlList": []}},
            ]
        }
        if images
        else None,
    }
    data = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": item}}}}
    return f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(data)}</script>'


@pytest.fixture
def tiktok_site(monkeypatch):
    """vt.tiktok.com 短链 302 到图文页；/video/{id} 返回带 imagePost 的页面。记下请求过的地址。"""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "vt.tiktok.com" and request.url.path == "/empty/":
            # 有的分享短链落到用户名为空的图文页
            return httpx.Response(301, headers={"location": f"https://www.tiktok.com/@/photo/{POST_ID}?_r=1"})
        if request.url.host == "vt.tiktok.com":
            return httpx.Response(301, headers={"location": f"{PHOTO}?_r=1"})
        if request.url.host == "vm.tiktok.com":
            return httpx.Response(301, headers={"location": "https://www.tiktok.com/@sam/video/1"})
        if request.url.path in {f"/@sam/video/{POST_ID}", f"/@/video/{POST_ID}"}:
            return httpx.Response(200, text=page())
        return httpx.Response(404)

    monkeypatch.setattr(
        tiktok, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
    )
    return seen


@pytest.mark.parametrize("url", [PHOTO, "https://vt.tiktok.com/ZSggcSUKX/"])
async def test_photo_post_recovers_from_unsupported_url(monkeypatch, tiktok_site, url):
    def extract(_url):
        raise RuntimeError(f"ERROR: Unsupported URL: {PHOTO}")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    info = await YtDlp().parse_share_url(url)
    assert [image.url for image in info.images] == [IMAGE, "https://p16/2.jpeg"]
    assert (info.source, info.title, info.cover_url) == ("tiktok", "三张图 #tag", IMAGE)
    assert info.music_url == "https://v19.tiktokcdn-us.com/m.mp4"
    assert (info.author.uid, info.author.name) == ("72", "Sam")
    assert info.page_url == PHOTO
    assert tiktok_site[-1] == f"https://www.tiktok.com/@sam/video/{POST_ID}"


async def test_short_link_to_photo_without_username(monkeypatch, tiktok_site):
    def extract(_url):
        raise RuntimeError("ERROR: Unsupported URL: https://www.tiktok.com/@/photo/1")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    info = await YtDlp().parse_share_url("https://vt.tiktok.com/empty/")
    assert [image.url for image in info.images] == [IMAGE, "https://p16/2.jpeg"]
    assert tiktok_site[-1] == f"https://www.tiktok.com/@/video/{POST_ID}"
    assert info.page_url == PHOTO


async def test_short_link_to_video_keeps_original_error(monkeypatch, tiktok_site):
    original = RuntimeError("ERROR: [TikTok] 1: Your IP address is blocked from accessing this post")

    def extract(_url):
        raise original

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(extract))
    with pytest.raises(RuntimeError) as exc:
        await YtDlp().parse_share_url("https://vm.tiktok.com/ZSx/")
    assert exc.value is original
    # 只跟了跳转，没去抓视频页
    assert tiktok_site == ["https://vm.tiktok.com/ZSx/"]


async def test_plain_video_link_does_not_touch_network(tiktok_site):
    assert await tiktok.image_post("https://www.tiktok.com/@sam/video/1") is None
    assert tiktok_site == []


@pytest.mark.parametrize(
    ("html", "post_id"), [(page(item_id="other"), POST_ID), (page(images=False), POST_ID), ("", "1")]
)
def test_page_without_matching_image_post_is_ignored(html, post_id):
    assert tiktok.find_item(html, post_id) is None


def test_tt_site_is_routed_to_tiktok_extractor():
    assert detect_source("https://www.tt.site/t/ZTfFKka4R/") == VideoSource.YtDlp
    assert tiktok.is_short("https://www.tt.site/t/ZTfFKka4R/")
    assert tiktok.is_short("https://www.tiktok.com/t/ZTfkvogxD/")
    assert not tiktok.is_short("https://www.tiktok.com/@sam/video/1")
