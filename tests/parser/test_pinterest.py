"""Pinterest 单帖解析：来源身份、媒体顺序、原图字段和只包含清单的视频。"""

import json

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.convert import net
from qiuyuantu.parser import base
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.pinterest import ROOT, Pinterest
from qiuyuantu.web.parse import _client_data

PIN = ROOT + "/pin/123/"
IMAGE = "https://i.pinimg.com/originals/aa/bb/original.png"
VIDEO = "https://v1.pinimg.com/videos/video.mp4"
HLS = "https://v1.pinimg.com/videos/master.m3u8"


@pytest.fixture
def responses(monkeypatch):
    routes, calls = {}, []

    def handle(request):
        calls.append(request)
        value = routes.get(str(request.url).split("?")[0], httpx.Response(404))
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    return routes, calls


def add_pin(routes, **values):
    pin = {"id": "123", "title": "Artwork", "images": {"orig": {"url": IMAGE}}, **values}
    routes[ROOT + "/resource/PinResource/get/"] = {"resource_response": {"data": pin}}
    return pin


@pytest.mark.parametrize(
    "url",
    [
        PIN,
        "https://www.pinterest.de/pin/123/",
        "https://pinterest.co.uk/pin/123/?a=1",
        "https://jp.pinterest.com/pin/123/",
    ],
)
async def test_native_route_and_original_image(responses, url):
    routes, calls = responses
    add_pin(routes, pinner={"full_name": "Artist"})
    info = await parser.parse_video_share_url(url)
    assert info.source == "pinterest" and info.images[0].url == IMAGE
    assert info.author.name == "Artist" and len(calls) == 1
    assert "cookie" not in calls[0].headers
    options = json.loads(calls[0].url.params["data"])["options"]
    assert options == {"id": "123", "field_set_key": "detailed"}


async def test_shortlink_only_accepts_a_pinterest_pin(responses):
    routes, calls = responses
    short = "https://api.pinterest.com/url_shortener/abc/redirect/"
    routes[short] = httpx.Response(302, headers={"location": PIN})
    add_pin(routes)
    assert (await Pinterest().parse_share_url("https://pin.it/abc")).images[0].url == IMAGE
    routes[short] = httpx.Response(302, headers={"location": "https://attacker.test/pin/123/"})
    with pytest.raises(ParseError, match="分享链接"):
        await Pinterest().parse_share_url("https://pin.it/abc")
    assert all(request.url.host != "attacker.test" for request in calls)


async def test_largest_available_image_is_not_rewritten_to_an_unverified_original(responses):
    routes, _ = responses
    url = "https://i.pinimg.com/736x/small.jpg?token=keep"
    add_pin(routes, images={"small": {"url": url, "width": 736, "height": 1000}})
    assert (await Pinterest().parse_share_url(PIN)).images[0].url == url


async def test_carousel_keeps_order_and_independent_videos_are_not_quality_tiers(responses):
    routes, _ = responses
    slots = [
        {"images": {"orig": {"url": IMAGE}}},
        {"video": {"video_list": {"x": {"url": VIDEO}}}},
        {"images": {"orig": {"url": IMAGE + "?last=1"}}},
    ]
    add_pin(routes, carousel_data={"carousel_slots": slots})
    info = await Pinterest().parse_share_url(PIN)
    assert [(item.kind, item.url) for item in info.assets] == [
        ("image", IMAGE),
        ("video", VIDEO),
        ("image", IMAGE + "?last=1"),
    ]
    assert not info.formats and not info.images and not info.video_url
    data = _client_data(info, PIN)
    assert all(net.verify(item.url, data["sig"][item.url]) for item in info.assets)


async def test_story_pages_include_every_media_block_and_ignore_text(responses):
    routes, _ = responses
    add_pin(
        routes,
        story_pin_data={
            "pages": [
                {
                    "blocks": [
                        {"type": "story_pin_paragraph_block", "text": "ignore"},
                        {"image": {"images": {"originals": {"url": IMAGE}}}},
                    ]
                },
                {"blocks": [{"video": {"video_list": {"hls": {"url": HLS}}}}]},
            ]
        },
    )
    info = await Pinterest().parse_share_url(PIN)
    assert [asset.url for asset in info.assets] == [IMAGE, HLS]


@pytest.mark.parametrize("hls_only", [False, True])
async def test_video_prefers_best_mp4_and_hls_is_a_server_download(responses, hls_only):
    routes, _ = responses
    files = {"hls": {"url": HLS, "height": 1080}}
    if not hls_only:
        files |= {"mp4": {"url": VIDEO, "height": 720}, "small": {"url": VIDEO + "?small=1", "height": 360}}
    add_pin(routes, videos={"video_list": files})
    info = await Pinterest().parse_share_url(PIN)
    if hls_only:
        assert not info.video_url and info.formats[0].format_spec == "bv*+ba/b/bv" and not info.formats[0].url
        assert info.page_url == HLS
    else:
        assert info.video_url == VIDEO and info.height == 720


async def test_page_fallback_matches_requested_pin_instead_of_recommendations(responses):
    routes, _ = responses
    pin = {"id": "123", "images": {"orig": {"url": IMAGE}}}
    unrelated = {"id": "456", "images": {"orig": {"url": IMAGE + "?wrong=1"}}}
    routes[PIN] = httpx.Response(
        200, text='<script type="application/json">' + json.dumps({"pins": [pin, unrelated]}) + "</script>"
    )
    info = await Pinterest().parse_share_url(PIN)
    assert info.images[0].url == IMAGE


async def test_relay_page_fallback_reads_only_json_and_normalizes_current_media_schema(responses):
    routes, _ = responses
    pin = {
        "entityId": "123",
        "id": "UGluOjEyMw==",
        "images_orig": {"url": IMAGE},
        "videos": {"videoList": {"v720P": {"url": VIDEO, "width": 1080, "height": 1920}}},
    }
    payload = {"data": {"v3GetPinQueryv2": {"data": pin}}}
    routes[PIN] = httpx.Response(
        200,
        text='<script>window.__PWS_RELAY_REGISTER_COMPLETED_REQUEST__("%7B%7D", '
        + json.dumps(payload)
        + ");window.untrusted()</script>",
    )
    info = await Pinterest().parse_share_url(PIN)
    assert info.video_url == VIDEO and info.width == 1080 and info.cover_url == IMAGE


async def test_gif_is_an_animation_asset_and_keeps_actual_format(responses):
    routes, _ = responses
    gif = IMAGE.replace(".png", ".gif")
    add_pin(routes, images={"orig": {"url": gif}})
    info = await Pinterest().parse_share_url(PIN)
    assert info.assets[0].kind == "animation" and info.assets[0].url == gif


async def test_relay_story_video_data_is_not_replaced_with_the_static_cover(responses):
    routes, _ = responses
    pin = {
        "entityId": "123",
        "images_orig": {"url": IMAGE},
        "storyPinData": {
            "pages": [{"blocks": [{"videoDataV2": {"videoList": {"vHLSV3": {"url": HLS, "thumbnail": IMAGE}}}}]}]
        },
    }
    payload = {"data": {"pin": pin}}
    routes[PIN] = httpx.Response(
        200, text='window.__PWS_RELAY_REGISTER_COMPLETED_REQUEST__("key", ' + json.dumps(payload) + ");"
    )
    info = await Pinterest().parse_share_url(PIN)
    assert len(info.assets) == 1 and info.assets[0].url == HLS and info.assets[0].kind == "video"
    assert not info.images


async def test_original_path_upgrade_is_used_only_after_a_successful_image_probe(responses):
    routes, calls = responses
    preview = "https://i.pinimg.com/736x/aa/photo.png"
    original = "https://i.pinimg.com/originals/aa/photo.png"
    add_pin(routes, images={"small": {"url": preview, "width": 736}})
    routes[original] = httpx.Response(200, headers={"content-type": "image/png"})
    info = await Pinterest().parse_share_url(PIN)
    assert info.images[0].url == info.cover_url == original
    assert len(calls) == 2 and calls[-1].method == "HEAD"


async def test_hls_selection_accepts_silent_video_instead_of_requiring_an_audio_track(responses):
    from yt_dlp import YoutubeDL

    routes, _ = responses
    add_pin(routes, videos={"video_list": {"hls": {"url": HLS, "height": 720}}})
    info = await Pinterest().parse_share_url(PIN)
    select = YoutubeDL({"quiet": True}).build_format_selector(info.formats[0].format_spec)
    silent = {"format_id": "hls", "url": HLS, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 720}
    chosen = list(select({"formats": [silent], "has_merged_format": False, "incomplete_formats": False}))
    assert len(chosen) == 1 and chosen[0]["url"] == HLS


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pinterest.com/artist/board/",
        "https://www.pinterest.com/search/pins/?q=test",
        "https://pinterest.com.evil.test/pin/123/",
    ],
)
async def test_non_pin_pages_are_rejected_without_network(responses, url):
    _, calls = responses
    with pytest.raises(ParseError):
        await Pinterest().parse_share_url(url)
    assert not calls


async def test_untrusted_media_and_excessive_carousels_are_rejected(responses):
    routes, _ = responses
    add_pin(routes, images={"orig": {"url": "https://pinimg.com.evil.test/file.png"}})
    with pytest.raises(ParseError, match="公开媒体"):
        await Pinterest().parse_share_url(PIN)
    add_pin(routes, carousel_data={"carousel_slots": [{}] * 101})
    with pytest.raises(ParseError, match="数量"):
        await Pinterest().parse_share_url(PIN)
