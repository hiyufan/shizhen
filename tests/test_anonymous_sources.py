"""匿名链路回放：作品身份、播放成功条件、公开媒体边界与凭证隔离。"""

import json

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.parser import base, bilibili
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.bilibili import BiliBili
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.telegram import CDN, Telegram, pack_name
from qiuyuantu.parser.wechat_channels import API, WeChatChannels, share_id

IMAGE = "https://finder.video.qq.com/cover.jpg?token=keep"
VIDEO = "https://finder.video.qq.com/video.mp4?token=keep"


@pytest.fixture
def public_http(monkeypatch):
    routes, calls = {}, []
    monkeypatch.setenv("PARSE_VIDEO_CHANNELS_PUBLIC_FALLBACK", "0")

    def handle(request):
        calls.append(request)
        reply = routes[request.url.path]
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply) if isinstance(reply, dict) else httpx.Response(200, text=reply)

    def client(**kw):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)

    monkeypatch.setattr(base, "create_async_client", client)
    monkeypatch.setattr(bilibili, "create_async_client", client)
    monkeypatch.setattr(BiliBili, "_buvid_cookie", "")

    async def noop(self):
        return None

    monkeypatch.setattr(BiliBili, "_ensure_buvid", noop)
    return routes, calls


def pack_page(name="HotCherry", rows=None):
    data = {
        "slug": name,
        "items": rows
        if rows is not None
        else [
            {"storageKey": "abc123" * 5, "format": "tgs", "emoji": "🙂"},
            {"storageKey": "def456" * 5, "format": "webp", "emoji": "😊"},
        ],
    }
    return '<h1>Hot Cherry</h1><dialog id="sticker-dialog" data-pack-data=\'' + json.dumps(data) + "'></dialog>"


async def test_telegram_archive_matches_pack_and_preserves_tgs_original(public_http):
    routes, calls = public_http
    routes["/telegram/hotcherry/"] = pack_page()
    info = await parser.parse_video_share_url("https://t.me/addstickers/HotCherry")
    assert info.source == "telegram" and info.title == "Hot Cherry"
    assert info.assets[0].url == CDN + "abc123" * 5 + ".tgs"
    assert info.assets[0].kind == "animation" and info.assets[0].ext == "tgs"
    assert info.assets[0].cover_url.endswith(".webp") and not info.images and not info.formats
    assert info.assets[1].kind == "image"
    assert len(calls) == 1 and "cookie" not in calls[0].headers and "authorization" not in calls[0].headers
    assert "Stickers.wiki" in info.notice
    assert parser.detect_source("https://t.me/public_channel/123") == VideoSource.YtDlp


@pytest.mark.parametrize(
    "page",
    [
        "<h1>Not found</h1>",
        pack_page("DifferentPack"),
        pack_page(rows=[{"storageKey": "../../private", "format": "tgs"}]),
        pack_page(rows=[{"storageKey": "a" * 32, "format": "exe"}]),
        pack_page(rows=[{"storageKey": "a" * 32, "format": "tgs"}] * 201),
    ],
)
async def test_telegram_never_returns_wrong_or_untrusted_archive(public_http, page):
    routes, _ = public_http
    routes["/telegram/hotcherry/"] = page
    with pytest.raises(ParseError):
        await Telegram().parse_video_id("HotCherry")


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/addstickers/../pack",
        "https://evil.example/addstickers/Pack",
        "https://t.me/addstickers/Pack/other",
    ],
)
def test_telegram_pack_url_is_exact(url):
    with pytest.raises(ParseError):
        pack_name(url)


async def test_channels_cover_only_uses_official_anonymous_short_uri(public_http):
    routes, calls = public_http
    routes["/finder-preview/api/feed/get_feed_info"] = {
        "errCode": 0,
        "data": {
            "errMsg": {"type": 0},
            "authorInfo": {"nickname": "Author"},
            "feedInfo": {"description": "Public share", "coverUrl": IMAGE},
        },
    }
    info = await parser.parse_video_share_url("https://weixin.qq.com/sph/PublicLink")
    assert info.source == "wechat-channels" and info.images[0].url == IMAGE
    assert not info.video_url and not info.formats and "只提供封面" in info.notice
    assert info.author.name == "Author"
    assert str(calls[0].url) == API and calls[0].method == "POST"
    assert json.loads(calls[0].content) == {"baseReq": {"generalToken": ""}, "shortUri": "PublicLink"}
    assert "cookie" not in calls[0].headers and "authorization" not in calls[0].headers


async def test_channels_only_uses_returned_video_not_guessed_cover_url(public_http):
    routes, _ = public_http
    routes["/finder-preview/api/feed/get_feed_info"] = {
        "errCode": 0,
        "data": {
            "feedInfo": {
                "coverUrl": IMAGE,
                "videoUrl": VIDEO,
                "h264VideoInfo": {"videoUrl": VIDEO},
                "h265VideoInfo": {"videoUrl": VIDEO + "&codec=hevc"},
            }
        },
    }
    info = await WeChatChannels().parse_video_id("PublicLink")
    assert info.video_url == VIDEO and info.formats[0].url == VIDEO + "&codec=hevc"
    assert info.images[0].url == IMAGE and not info.notice


@pytest.mark.parametrize(
    "data",
    [
        {"errCode": -1},
        {"errCode": 0, "data": {"errMsg": {"type": 2}}},
        {"errCode": 0, "data": {"feedInfo": {"coverUrl": "https://127.0.0.1/image"}}},
        {"errCode": 0, "data": {"feedInfo": {}}},
    ],
)
async def test_channels_rejects_restricted_empty_and_untrusted_responses(public_http, data):
    routes, _ = public_http
    routes["/finder-preview/api/feed/get_feed_info"] = data
    with pytest.raises(ParseError):
        await WeChatChannels().parse_video_id("PublicLink")


@pytest.mark.parametrize(
    "url",
    [
        "https://weixin.qq.com/sph/../x",
        "https://weixin.qq.com.evil.example/sph/Public",
        "https://weixin.qq.com/other/Public",
    ],
)
def test_channels_rejects_unsupported_share_links(url):
    with pytest.raises(ParseError):
        share_id(url)


async def test_bili_412_view_switches_to_matching_wbi_response(public_http):
    routes, calls = public_http
    routes["/x/web-interface/view"] = httpx.Response(412)
    routes["/x/web-interface/wbi/view"] = {"code": 0, "data": {"bvid": "BV1test", "title": "Correct"}}
    result = await BiliBili()._api("https://api.bilibili.com/x/web-interface/view?bvid=BV1test")
    assert result["data"]["title"] == "Correct" and len(calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        {"code": 0, "data": {"bvid": "BVdifferent"}},
        {"code": 0, "data": {"bvid": "BV1Test"}},
        "<html>security check</html>",
    ],
)
async def test_bili_does_not_accept_different_video_from_fallback(public_http, response):
    routes, _ = public_http
    routes["/x/web-interface/view"] = httpx.Response(412)
    routes["/x/web-interface/wbi/view"] = response
    with pytest.raises(ParseError) as error:
        await BiliBili()._api("https://api.bilibili.com/x/web-interface/view?bvid=BV1test")
    assert error.value.reason == "blocked"


async def test_bili_metadata_block_cannot_hide_verified_playable_part(public_http):
    routes, _ = public_http
    routes["/x/web-interface/view"] = httpx.Response(412)
    routes["/x/web-interface/wbi/view"] = httpx.Response(412)
    routes["/x/player/pagelist"] = {
        "code": 0,
        "data": [
            {"cid": 1, "part": "First", "duration": 10},
            {"cid": 2, "part": "Second", "duration": 20},
        ],
    }
    routes["/x/player/wbi/playurl"] = {"code": 0, "data": {"quality": 64, "durl": [{"url": VIDEO}]}}
    info = await BiliBili().parse_video_id("BV1test", 2)
    assert info.video_url == VIDEO and info.duration == 20 and "Second" in info.title
    assert info.page_url.endswith("?p=2")


@pytest.mark.parametrize("code", [-404, -10403])
async def test_bili_blocked_metadata_with_failed_playback_stays_failure(public_http, code):
    routes, _ = public_http
    routes["/x/web-interface/view"] = httpx.Response(412)
    routes["/x/web-interface/wbi/view"] = httpx.Response(412)
    routes["/x/player/pagelist"] = {"code": 0, "data": [{"cid": 1, "part": "Title"}]}
    routes["/x/player/wbi/playurl"] = {"code": code}
    with pytest.raises(ParseError):
        await BiliBili().parse_video_id("BV1test")
