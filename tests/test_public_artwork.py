"""公开素材接口离线回放：精确匹配目标，保留真实媒体格式，不请求账号或音频。"""

import json

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.parser import base
from qiuyuantu.parser.applemusic import AppleMusic, page_url
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.hoyoplay import API, GAMES, HoYoPlay, game_from_url, game_url
from qiuyuantu.parser.steam import CDN, Steam, asset_url, reward_id

STEAM_PAGE = "https://store.steampowered.com/points/shop/app/42/reward/100"
STEAM_API = "https://api.steampowered.com/ILoyaltyRewardsService/QueryRewardItems/v1/"
APPLE_PAGE = "https://music.apple.com/us/album/example/100"
APPLE_IMAGE = "https://is1-ssl.mzstatic.com/image/thumb/art/{w}x{h}.{f}"
APPLE_VIDEO = "https://mvod.itunes.apple.com/example/master.m3u8"
HOYO_IMAGE = "https://launcher-webstatic.mihoyo.com/background.webp"
HOYO_VIDEO = "https://launcher-webstatic.mihoyo.com/background.webm"


@pytest.fixture
def public_http(monkeypatch):
    routes, calls = {}, []

    def handle(request):
        calls.append(request)
        reply = routes[str(request.url).split("?")[0]]
        return httpx.Response(200, json=reply) if isinstance(reply, dict) else httpx.Response(200, text=reply)

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(base, "create_async_client", client)
    return routes, calls


def steam_item(*, animated=True, kind=14):
    return {
        "appid": 42,
        "defid": 100,
        "community_item_class": kind,
        "community_item_data": {
            "item_title": "Example",
            "animated": animated,
            "item_image_small": "animation.png",
            "item_image_large": "still.jpg",
            "item_movie_mp4": "",
            "item_movie_webm": "",
        },
    }


@pytest.mark.parametrize("kind", [12, 14, 15])
async def test_steam_uses_animation_instead_of_large_still(public_http, kind):
    routes, calls = public_http
    item = steam_item(kind=kind)
    if kind == 15:
        item["community_item_data"]["item_image_small"] = "avatar.gif"
    routes[STEAM_API] = {"response": {"definitions": [item]}}
    info = await Steam().parse_share_url(STEAM_PAGE)
    assert info.cover_url == f"{CDN}/42/" + ("avatar.gif" if kind == 15 else "animation.png")
    assert info.images[0].url == info.cover_url and not info.video_url
    assert json.loads(calls[0].url.params["input_json"])["definitionids"] == [100]
    assert len(calls) == 1 and "cookie" not in calls[0].headers
    assert parser.detect_source(STEAM_PAGE) == VideoSource.Steam


async def test_steam_preserves_static_art_and_both_video_containers(public_http):
    routes, _ = public_http
    item = steam_item(animated=False)
    item["community_item_data"].update(item_movie_mp4="46/movie/asset.mp4", item_movie_webm="46/movie/asset.webm")
    routes[STEAM_API] = {"response": {"definitions": [item]}}
    info = await Steam().parse_share_url(STEAM_PAGE)
    assert info.cover_url.endswith("/still.jpg") and info.video_url.endswith("/46/movie/asset.mp4")
    assert info.formats[0].url.endswith(".webm") and info.formats[0].ext == "webm"


@pytest.mark.parametrize("field", ["defid", "appid"])
async def test_steam_does_not_return_a_different_item(public_http, field):
    routes, _ = public_http
    item = steam_item()
    item[field] = 999
    routes[STEAM_API] = {"response": {"definitions": [item]}}
    with pytest.raises(ParseError, match="这个物品"):
        await Steam().parse_share_url(STEAM_PAGE)


@pytest.mark.parametrize(
    "url",
    [
        "https://store.steampowered.com/points/shop/",
        "https://store.steampowered.com/app/42",
        "https://store.steampowered.com.evil.test/points/shop/reward/100",
        "file:///points/shop/reward/100",
    ],
)
def test_steam_requires_an_individual_reward(url):
    with pytest.raises(ParseError):
        reward_id(url)


@pytest.mark.parametrize("name", ["../private", "https://evil.test/a", "/x.png", "a//b", "x?query", "a/./b"])
def test_steam_rejects_untrusted_asset_paths(name):
    with pytest.raises(ParseError):
        asset_url(42, name)


def hoyo_response(*, video=True):
    return {
        "retcode": 0,
        "data": {
            "game_info_list": [
                {
                    "game": {"biz": "wrong_game"},
                    "backgrounds": [{"background": {"url": "https://evil.test/other.webp"}}],
                },
                {
                    "game": {"biz": "hkrpg_cn"},
                    "backgrounds": [
                        {
                            "background": {"url": HOYO_IMAGE},
                            "theme": {"url": HOYO_IMAGE},
                            "video": {"url": HOYO_VIDEO if video else ""},
                        },
                    ],
                },
            ]
        },
    }


@pytest.mark.parametrize("video", [False, True])
async def test_hoyoplay_selects_game_and_deduplicates_images(public_http, video):
    routes, calls = public_http
    routes[API] = hoyo_response(video=video)
    info = await HoYoPlay().parse_share_url(game_url("star-rail"))
    assert info.title == "崩坏：星穹铁道 · HoYoPlay"
    assert [i.url for i in info.images] == [HOYO_IMAGE]
    assert info.video_url == (HOYO_VIDEO if video else "")
    assert not info.formats and not info.images[0].live_photo_url
    assert calls[0].url.params["game_id"] == GAMES["star-rail"][1]
    assert "cookie" not in calls[0].headers
    assert parser.detect_source(game_url("star-rail")) == VideoSource.HoYoPlay


@pytest.mark.parametrize("url", [API, API + "?game_id=arbitrary", "https://evil.test" + API[API.index("/hyp/") :]])
def test_hoyoplay_only_accepts_known_games(url):
    with pytest.raises(ParseError):
        game_from_url(url)


async def test_hoyoplay_rejects_non_official_assets(public_http):
    routes, _ = public_http
    data = hoyo_response()
    data["data"]["game_info_list"][1]["backgrounds"][0]["video"]["url"] = "https://evil.test/video.webm"
    routes[API] = data
    with pytest.raises(ParseError, match="壁纸地址"):
        await HoYoPlay().parse_video_id("star-rail")


def apple_html(*, animated=True, kind="containerDetailHeaderLockup", image=APPLE_IMAGE):
    item = {
        "title": "Selected work",
        "artwork": {"dictionary": {"url": image, "width": 3000, "height": 3000}},
        "subtitleLinks": [{"title": "Artist"}],
        "videoArtwork": {"dictionary": {"motionDetailSquare": {"video": APPLE_VIDEO}} if animated else {}},
    }
    sections = [
        {"itemKind": "trackLockup", "items": [{"title": "Wrong track", "artwork": {"url": image}}]},
        {"itemKind": kind, "items": [item]},
    ]
    return (
        '<script id="serialized-server-data" type="application/json">'
        + json.dumps({"data": [{"data": {"sections": sections}}]})
        + "</script>"
    )


@pytest.mark.parametrize(
    ("path", "kind", "animated"),
    [
        ("album/example/100?i=200", "containerDetailHeaderLockup", True),
        ("song/100", "songDetailHeader", False),
        ("playlist/example/pl.abcdef", "containerDetailHeaderLockup", True),
    ],
)
async def test_apple_reads_work_header_not_first_track(public_http, path, kind, animated):
    routes, calls = public_http
    url = "https://music.apple.com/us/" + path
    routes[page_url(url)] = apple_html(kind=kind, animated=animated)
    info = await AppleMusic().parse_share_url(url)
    assert info.title == "Selected work" and info.cover_url.endswith("/3000x3000.jpg")
    assert not info.video_url and not info.music_url
    assert info.page_url == page_url(url) and len(calls) == 1
    assert bool(info.formats) is animated
    if animated:
        assert info.formats[0].format_spec == "apple-artwork:motionDetailSquare"
        assert info.formats[0].ext == "mp4" and not info.formats[0].url
    assert parser.detect_source(url) == VideoSource.AppleMusic


async def test_apple_missing_header_never_falls_back_to_track(public_http):
    routes, _ = public_http
    routes[APPLE_PAGE] = apple_html(kind="trackLockup")
    with pytest.raises(ParseError, match="公开封面"):
        await AppleMusic().parse_share_url(APPLE_PAGE)


async def test_apple_rejects_untrusted_artwork(public_http):
    routes, _ = public_http
    routes[APPLE_PAGE] = apple_html(image="https://127.0.0.1/private")
    with pytest.raises(ParseError, match="封面地址"):
        await AppleMusic().parse_share_url(APPLE_PAGE)


@pytest.mark.parametrize(
    "url",
    [
        "https://music.apple.com/us/artist/example/100",
        "https://music.apple.com.evil.test/us/album/100",
        "file:///us/album/100",
        "https://music.apple.com/us/album/../100",
    ],
)
def test_apple_requires_a_work_link(url):
    with pytest.raises(ParseError):
        page_url(url)
