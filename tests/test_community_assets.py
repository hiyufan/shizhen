"""公开社区与启动器素材：请求身份、资源边界及混合素材 API 契约。"""

import dataclasses

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.convert import net
from qiuyuantu.parser import base
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.bilibili_assets import COLLECTION_API, SUIT_API, BiliAssets, asset_ref
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.miyoushe import API, MiYouShe, post_ref
from qiuyuantu.parser.wutheringwaves import CONFIG_URL, PROJECT, ROOT, WutheringWaves
from qiuyuantu.web.parse import _client_data, _edge_urls

IMAGE = "https://i0.hdslb.com/bfs/garb/card.png"
VIDEO = "https://i0.hdslb.com/bfs/garb/movie.mp4"
POST_IMAGE = "https://upload-bbs.miyoushe.com/upload/original.gif?token=keep"
WUWA_VIDEO = "https://hw-pcdownload-qcloud.aki-game.net/launcher/clientUpload/background.mp4"
WUWA_IMAGE = "https://hw-pcdownload-qcloud.aki-game.net/launcher/clientUpload/first.webp"
SUIT_PAGE = "https://www.bilibili.com/h5/mall/suit/detail?id=42"
COLLECTION_PAGE = "https://www.bilibili.com/h5/mall/digital-card/home?act_id=111&lottery_id=15"
POST_PAGE = "https://www.miyoushe.com/ys/article/123"


@pytest.fixture
def public_http(monkeypatch):
    routes, calls = {}, []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=routes[str(request.url).split("?")[0]])

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    return routes, calls


@pytest.mark.parametrize("kind", [1, 2])
async def test_wuwa_uses_current_version_and_actual_background_type(public_http, kind):
    routes, calls = public_http
    routes[CONFIG_URL] = {"functionCode": {"background": "current-version"}}
    routes[ROOT + PROJECT + "background/current-version/en.json"] = {
        "functionSwitch": 1,
        "backgroundFileType": kind,
        "backgroundFile": WUWA_VIDEO if kind == 2 else WUWA_IMAGE,
        "firstFrameImage": WUWA_IMAGE,
    }
    info = await parser.parse_video_share_url(CONFIG_URL)
    assert info.source == "wutheringwaves"
    assert info.video_url == (WUWA_VIDEO if kind == 2 else "")
    assert info.images[0].url == WUWA_IMAGE
    assert len(calls) == 2 and all("cookie" not in request.headers for request in calls)


@pytest.mark.parametrize("version", ["../secret", "https://evil.example/", ""])
async def test_wuwa_rejects_invalid_config_version_before_second_request(public_http, version):
    routes, calls = public_http
    routes[CONFIG_URL] = {"functionCode": {"background": version}}
    with pytest.raises(ParseError):
        await WutheringWaves().parse_video_id("global")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "override", [{"functionSwitch": 0}, {"backgroundFileType": 99}, {"backgroundFile": "https://evil.example/a.mp4"}]
)
async def test_wuwa_rejects_disabled_unknown_or_untrusted_background(public_http, override):
    routes, _ = public_http
    routes[CONFIG_URL] = {"functionCode": {"background": "v1"}}
    routes[ROOT + PROJECT + "background/v1/en.json"] = {
        "functionSwitch": 1,
        "backgroundFileType": 2,
        "backgroundFile": WUWA_VIDEO,
        **override,
    }
    with pytest.raises(ParseError):
        await WutheringWaves().parse_share_url(CONFIG_URL)


def post_data():
    return {
        "retcode": 0,
        "data": {
            "post": {
                "post": {"post_id": "123", "game_id": 2, "subject": "Gallery", "images": [POST_IMAGE, POST_IMAGE]},
                "user": {"nickname": "Author", "avatar_url": "https://unrelated.example/avatar.png"},
                "cover": {"url": "https://unrelated.example/cover.png"},
            }
        },
    }


async def test_miyoushe_preserves_body_originals_and_sends_referer(public_http):
    routes, calls = public_http
    routes[API] = post_data()
    info = await parser.parse_video_share_url(POST_PAGE)
    assert info.source == "miyoushe" and info.title == "Gallery"
    assert [image.url for image in info.images] == [POST_IMAGE]
    assert not info.video_url and info.author.name == "Author"
    assert calls[0].url.params["post_id"] == "123"
    assert calls[0].headers["referer"] == "https://www.miyoushe.com/"
    assert "cookie" not in calls[0].headers


@pytest.mark.parametrize("change", ["mismatch", "deleted", "empty", "untrusted", "restricted"])
async def test_miyoushe_rejects_unusable_post_instead_of_using_cover(public_http, change):
    routes, _ = public_http
    data = post_data()
    core = data["data"]["post"]["post"]
    if change == "mismatch":
        core["post_id"] = "456"
    elif change == "deleted":
        core["is_deleted"] = True
    elif change == "empty":
        core["images"] = []
    elif change == "untrusted":
        core["images"] = ["https://upload-bbs.miyoushe.com.evil.example/a.png"]
    else:
        data["retcode"] = 1034
    routes[API] = data
    with pytest.raises(ParseError):
        await MiYouShe().parse_share_url(POST_PAGE)


def test_miyoushe_fragment_link_and_platform_detection():
    assert post_ref("https://m.miyoushe.com/sr/#/article/123") == ("123", 6)
    assert parser.detect_source(SUIT_PAGE) == VideoSource.BiliAssets
    assert parser.detect_source(COLLECTION_PAGE) == VideoSource.BiliAssets
    assert parser.detect_source("https://www.bilibili.com/video/BV1test") == VideoSource.BiliBili


@pytest.mark.parametrize("url", ["https://evil.example/ys/article/123", "https://www.miyoushe.com/ys/home/123"])
def test_miyoushe_rejects_non_post_links(url):
    with pytest.raises(ParseError):
        post_ref(url)


async def test_suit_keeps_independent_assets_out_of_quality_and_live_fields(public_http):
    routes, calls = public_http
    routes[SUIT_API] = {
        "code": 0,
        "data": {
            "item": {"item_id": 42, "name": "Suit", "properties": {"image_cover": IMAGE}},
            "suit_items": {
                "skin": [{"name": "Background", "properties": {"head_myself_mp4_bg": VIDEO}}],
                "emoji_package": [{"items": [{"name": "Sticker", "properties": {"image": IMAGE}}]}],
            },
        },
    }
    info = await parser.parse_video_share_url(SUIT_PAGE)
    assert not info.video_url and not info.formats and not info.images
    assert {asset.url for asset in info.assets} == {IMAGE, VIDEO}
    assert next(asset for asset in info.assets if asset.url == VIDEO).kind == "video"
    assert calls[0].url.params["item_id"] == "42" and "cookie" not in calls[0].headers
    data = _client_data(info, SUIT_PAGE)
    assert {IMAGE, VIDEO, info.page_url} <= data["sig"].keys()
    assert all(net.verify(url, sig) for url, sig in data["sig"].items())


async def test_collection_selects_one_cdn_per_card_and_preserves_signed_url(public_http):
    routes, _ = public_http
    signed = VIDEO + "?deadline=123&sig=keep"
    routes[COLLECTION_API] = {
        "code": 0,
        "data": {
            "lottery_id": 15,
            "name": "Collection",
            "item_list": [
                {
                    "card_info": {
                        "card_name": "Card",
                        "card_img": IMAGE,
                        "video_list": [signed, VIDEO],
                        "card_img_download": "https://unrelated.example/watermarked.png",
                    }
                },
            ],
        },
    }
    info = await parser.parse_video_share_url(COLLECTION_PAGE)
    assert [(a.url, a.kind) for a in info.assets] == [(IMAGE, "image"), (signed, "video")]
    assert info.assets[1].cover_url == IMAGE and not info.formats


@pytest.mark.parametrize(
    "data",
    [
        {"item_id": 9, "properties": {"image": IMAGE}},
        {"item_id": 42, "properties": {"image": "https://127.0.0.1/x.png"}},
        {"item_id": 42, "properties": {"image_ani": "https://i0.hdslb.com/a.bin"}},
        {},
    ],
)
async def test_suit_rejects_mismatch_bad_hosts_and_unsupported_assets(public_http, data):
    routes, _ = public_http
    routes[SUIT_API] = {"code": 0, "data": data}
    with pytest.raises(ParseError):
        await BiliAssets().parse_share_url(SUIT_PAGE)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/h5/mall/digital-card/home?act_id=111",
        "https://www.bilibili.com/h5/mall/suit/detail?id=../42",
        "https://www.bilibili.com.evil.example/h5/mall/suit/detail?id=42",
    ],
)
def test_bili_assets_requires_exact_page_and_complete_ids(url):
    with pytest.raises(ParseError):
        asset_ref(url)


def test_asset_edge_urls_are_classified_by_media_kind(monkeypatch):
    from qiuyuantu.web import parse

    monkeypatch.setattr(parse.relay, "edge_img_enabled", lambda: True)
    monkeypatch.setattr(parse.relay, "edge_media_enabled", lambda: True)
    monkeypatch.setattr(parse.relay, "edge_img_url", lambda url, ttl: "image:" + url)
    monkeypatch.setattr(parse.relay, "edge_media_url", lambda url, ttl: "video:" + url)
    info = base.VideoInfo(
        video_url="",
        cover_url="",
        assets=[
            base.MediaAsset(url=VIDEO, kind="video", cover_url=IMAGE),
            base.MediaAsset(url=POST_IMAGE, kind="animation"),
        ],
    )
    edges = _edge_urls(dataclasses.asdict(info))
    assert edges == {VIDEO: "video:" + VIDEO, IMAGE: "image:" + IMAGE, POST_IMAGE: "image:" + POST_IMAGE}
