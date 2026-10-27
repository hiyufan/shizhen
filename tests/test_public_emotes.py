"""公开素材来源的身份匹配、原格式与请求边界。"""

import copy

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.parser import base
from qiuyuantu.parser.errors import ParseError

SEVEN = "01FSNK0GP000038GDBZ91HT6SQ"
BTTV = "566ca38765dbbdab32ec0560"
SEVEN_DATA = {
    "id": SEVEN,
    "name": "PETPET",
    "animated": True,
    "host": {"url": "//cdn.7tv.app/emote/" + SEVEN, "files": [{"name": "4x.webp", "format": "WEBP", "width": 128}]},
}
BTTV_DATA = {"id": BTTV, "code": "SourPls", "imageType": "gif"}
FFZ_DATA = {
    "id": 9,
    "name": "ZrehplaR",
    "urls": {"1": "//cdn.frankerfacez.com/emote/9/1", "4": "//cdn.frankerfacez.com/emote/9/4"},
}


@pytest.fixture
def public_http(monkeypatch):
    state = {"calls": [], "routes": {}}

    def handle(request):
        state["calls"].append(request)
        value = state["routes"][str(request.url)]
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=copy.deepcopy(value))

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    return state


@pytest.mark.parametrize(
    "site,url,endpoint,data,extension",
    [
        ("7tv", "https://7tv.app/emotes/" + SEVEN, "https://7tv.io/v3/emotes/" + SEVEN, SEVEN_DATA, "webp"),
        (
            "bttv",
            "https://betterttv.com/emotes/" + BTTV,
            "https://api.betterttv.net/3/emotes/" + BTTV,
            BTTV_DATA,
            "gif",
        ),
        (
            "ffz",
            "https://www.frankerfacez.com/emoticon/9-ZrehplaR",
            "https://api.frankerfacez.com/v1/emote/9",
            {"emote": FFZ_DATA},
            "",
        ),
    ],
)
async def test_single_emote_preserves_identity_and_original_type(public_http, site, url, endpoint, data, extension):
    public_http["routes"][endpoint] = data
    info = await parser.parse_video_share_url(url)
    assert info.source == site and len(info.assets) == 1
    assert not info.video_url and not info.formats
    assert info.assets[0].ext == extension
    request = public_http["calls"][0]
    assert "authorization" not in request.headers and "cookie" not in request.headers


async def test_bttv_global_fallback_only_accepts_matching_id(public_http):
    public_http["routes"]["https://api.betterttv.net/3/emotes/" + BTTV] = httpx.Response(403)
    public_http["routes"]["https://api.betterttv.net/3/cached/emotes/global"] = [BTTV_DATA]
    info = await parser.parse_video_share_url("https://betterttv.com/emotes/" + BTTV)
    assert info.assets[0].url.endswith("/" + BTTV + "/3x")
    public_http["routes"]["https://api.betterttv.net/3/cached/emotes/global"] = [BTTV_DATA | {"id": "0" * 24}]
    with pytest.raises(ParseError, match="无法匿名读取"):
        await parser.parse_video_share_url("https://betterttv.com/emotes/" + BTTV)


async def test_seven_set_is_bounded_and_cdn_cannot_point_elsewhere(public_http):
    endpoint = "https://7tv.io/v3/emote-sets/global"
    public_http["routes"][endpoint] = {"name": "global", "emotes": [{"data": SEVEN_DATA}]}
    info = await parser.parse_video_share_url("https://7tv.app/emote-sets/global")
    assert info.assets[0].kind == "animation"
    public_http["routes"][endpoint]["emotes"] *= 201
    with pytest.raises(ParseError, match="200"):
        await parser.parse_video_share_url("https://7tv.app/emote-sets/global")
    bad = copy.deepcopy(SEVEN_DATA)
    bad["host"]["url"] = "https://127.0.0.1/private"
    public_http["routes"]["https://7tv.io/v3/emotes/" + SEVEN] = bad
    with pytest.raises(ParseError, match="地址无效"):
        await parser.parse_video_share_url("https://7tv.app/emotes/" + SEVEN)


async def test_static_seven_prefers_lossless_png_at_the_highest_actual_dimensions(public_http):
    data = copy.deepcopy(SEVEN_DATA)
    data["animated"] = False
    data["host"]["files"] = [
        {"name": "4x.webp", "format": "WEBP", "width": 128, "height": 128},
        {"name": "4x.png", "format": "PNG", "width": 128, "height": 128},
        {"name": "2x.png", "format": "PNG", "width": 64, "height": 64},
    ]
    public_http["routes"]["https://7tv.io/v3/emotes/" + SEVEN] = data
    info = await parser.parse_video_share_url("https://7tv.app/emotes/" + SEVEN)
    assert info.assets[0].url.endswith("/4x.png") and info.assets[0].ext == "png"
    assert info.assets[0].kind == "image"


async def test_animated_seven_does_not_choose_a_static_png_preview(public_http):
    data = copy.deepcopy(SEVEN_DATA)
    data["host"]["files"].append({"name": "4x.png", "format": "PNG", "width": 128})
    public_http["routes"]["https://7tv.io/v3/emotes/" + SEVEN] = data
    info = await parser.parse_video_share_url("https://7tv.app/emotes/" + SEVEN)
    assert info.assets[0].url.endswith("/4x.webp") and info.assets[0].kind == "animation"


async def test_line_uses_only_listed_previews_and_skips_cover(public_http):
    import html
    import json

    cdn = "https://stickershop.line-scdn.net/"
    cover = {"id": "42", "staticUrl": cdn + "cover.png"}
    sticker = {
        "id": "900",
        "staticUrl": cdn + "static.png",
        "animationUrl": cdn + "animation.png",
        "soundUrl": cdn + "sound.m4a",
    }
    rows = "".join(
        '<li data-preview="' + html.escape(json.dumps(row), quote=True) + '"></li>' for row in (cover, sticker)
    )
    public_http["routes"]["https://store.line.me/stickershop/product/42/en"] = httpx.Response(200, text=rows)
    info = await parser.parse_video_share_url("https://line.me/S/sticker/42/?lang=zh-Hant")
    assert len(info.assets) == 1 and info.assets[0].url == sticker["animationUrl"]
    assert info.assets[0].kind == "animation" and info.assets[0].ext == "png"
    assert len(public_http["calls"]) == 1


async def test_line_rejects_foreign_preview_hosts(public_http):
    public_http["routes"]["https://store.line.me/stickershop/product/42/en"] = httpx.Response(
        200, text="""<li data-preview='{"id":"900","staticUrl":"https://evil.example/a.png"}'></li>"""
    )
    with pytest.raises(ParseError, match="地址无效"):
        await parser.parse_video_share_url("https://store.line.me/stickershop/product/42/en")


async def test_noto_only_lists_available_official_variants(public_http):
    root = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f929/"
    public_http["routes"][root + "512.webp"] = httpx.Response(200, headers={"content-type": "image/webp"})
    public_http["routes"][root + "512.gif"] = httpx.Response(200, headers={"content-type": "image/gif"})
    public_http["routes"][root + "lottie.json"] = httpx.Response(404)
    info = await parser.parse_video_share_url(root + "512.gif")
    assert [a.ext for a in info.assets] == ["webp", "gif"]
    assert "CC BY 4.0" in info.notice
    assert all(request.method == "HEAD" for request in public_http["calls"])


@pytest.mark.parametrize(
    "url",
    [
        "https://evil7tv.app/emotes/" + SEVEN,
        "https://betterttv.com/emotes/../../private",
        "https://store.line.me/officialaccount",
        "https://fonts.gstatic.com/other/1f929/512.webp",
    ],
)
async def test_similar_domains_and_unrelated_routes_are_not_sources(public_http, url):
    if "evil7tv" in url:
        assert parser.detect_source(url).value == "ytdlp"
        return
    with pytest.raises(ParseError):
        await parser.parse_video_share_url(url)
    assert not public_http["calls"]
