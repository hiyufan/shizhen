"""微视目标作品与推荐列表分开，下载保留真实高清档与兼容预览。"""

import copy
import json

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.parser import base, image_probe, mp4_probe
from qiuyuantu.parser.base import FormatInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.quality import best_video_format
from qiuyuantu.parser.weishi import WeiShi

_API = "https://api.weishi.qq.com/trpc.weishi.weishi_h5_proxy.weishi_h5_proxy/WSH5GetPlayPage"
_LOW = "https://v.weishi.qq.com/work.f70.mp4?sig=keep&wsadapt=0"
_HIGH = "https://v.weishi.qq.com/work.f207314.mp4?sig=high"


def _feed(ident="work42"):
    return {
        "id": ident,
        "feed_desc_withat": "目标作品",
        "poster": {"id": "author42", "nick": "作者"},
        "video": {"width": 720, "height": 1280, "duration": 10751},
        "video_url": _LOW,
        "video_spec_urls": {
            "0": {"url": _LOW, "width": 720, "height": 1280, "fps": 30, "videoCoding": 1},
            "43": {
                "url": _HIGH,
                "width": 1080,
                "height": 1920,
                "fps": 30,
                "externInfo": {"video_encoder": "t265"},
            },
        },
    }


@pytest.fixture
def weishi_http(monkeypatch):
    state = {
        "body": {"rsp_header": {"ret": 0}, "rsp_body": {"feeds": [_feed()]}},
        "calls": [],
        "probed": [],
        "images": {},
    }

    def reply(request):
        state["calls"].append(request)
        if request.url.host == "video.weishi.qq.com":
            return httpx.Response(302, headers={"location": "https://isee.weishi.qq.com/share?id=work42"})
        if request.url.host == "isee.weishi.qq.com":
            return httpx.Response(200, text="share page")
        assert str(request.url) == _API
        return httpx.Response(200, json=copy.deepcopy(state["body"]))

    async def probe(url, headers):
        state["probed"].append((url, headers))

    async def image_size(url, _headers):
        return state["images"].get(url)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(mp4_probe, "probe", probe)
    monkeypatch.setattr(image_probe, "probe", image_size)
    return state


async def test_modern_guest_rpc_downloads_1080_hevc_with_720_avc_preview(weishi_http):
    info = await WeiShi().parse_video_id("work42")
    best = best_video_format(info)
    assert (best.url, best.width, best.height, best.codec) == (_HIGH, 1080, 1920, "H.265")
    assert (info.video_url, info.width, info.height, info.video_codec) == (_LOW, 720, 1280, "H.264")
    assert info.duration == 10.751 and info.author.uid == "author42" and info.title == "目标作品"
    request = weishi_http["calls"][0]
    body = json.loads(request.content)
    assert request.method == "POST" and request.headers["referer"] == "https://isee.weishi.qq.com/"
    assert body["req_body"]["feedid"] == "work42" and body["req_body"]["recommendtype"] == 0
    assert body["req_header"]["authInfo"] == {"authType": 0}
    assert "cookie" not in request.headers and "authorization" not in request.headers


async def test_aliases_do_not_duplicate_downloads_or_strip_selected_signatures(weishi_http):
    row = weishi_http["body"]["rsp_body"]["feeds"][0]
    row["video_spec_urls"]["1"] = row["video_spec_urls"]["0"] | {"url": _LOW.replace("wsadapt=0", "wsadapt=1")}
    info = await WeiShi().parse_video_id("work42")
    assert info.video_url == _LOW and len(info.formats) == 1
    assert [url for url, _ in weishi_http["probed"]] == [_LOW, _HIGH]
    assert all(h["User-Agent"] == info.video_headers["User-Agent"] for _, h in weishi_http["probed"])


async def test_actual_mp4_dimensions_override_claimed_4k_and_frame_rate(weishi_http, monkeypatch):
    row = weishi_http["body"]["rsp_body"]["feeds"][0]
    row["video_spec_urls"]["43"] |= {"width": 2160, "height": 3840, "fps": 120}

    async def probe(url, _headers):
        if url == _HIGH:
            return FormatInfo(url=url, width=1080, height=1920, fps=30, codec="H.265")
        return FormatInfo(url=url, width=720, height=1280, fps=60, codec="H.264")

    monkeypatch.setattr(mp4_probe, "probe", probe)
    best = best_video_format(await WeiShi().parse_video_id("work42"))
    assert (best.width, best.height, best.fps) == (1080, 1920, 30)


async def test_same_dimensions_keep_high_frame_rate_even_under_an_earlier_key(weishi_http):
    row = weishi_http["body"]["rsp_body"]["feeds"][0]
    row["video_spec_urls"] = {
        "0": row["video_spec_urls"]["0"] | {"url": _HIGH, "fps": 60},
        "999": row["video_spec_urls"]["0"],
    }
    best = best_video_format(await WeiShi().parse_video_id("work42"))
    assert best.url == _HIGH and best.fps == 60


async def test_unrelated_first_feed_and_recommendation_are_never_substituted(weishi_http):
    data = weishi_http["body"]["rsp_body"]
    data["feeds"].insert(0, _feed("other42"))
    info = await WeiShi().parse_video_id("work42")
    assert info.title == "目标作品"
    data["recommendfeeds"] = [data["feeds"].pop()]
    weishi_http["probed"].clear()
    with pytest.raises(ParseError, match="链接对应的作品"):
        await WeiShi().parse_video_id("work42")
    assert not weishi_http["probed"]


@pytest.mark.parametrize(
    "body,reason",
    [
        ({"rsp_header": {"ret": -10000}}, "restricted"),
        ({"rsp_header": {"ret": 0}, "rsp_body": {"isdeleted": 1}}, "deleted"),
        (
            {"rsp_header": {"ret": 0}, "rsp_body": {"feedResultInfo": {"feedResults": {"work42": {"ret": 2}}}}},
            "restricted",
        ),
    ],
)
async def test_platform_errors_do_not_return_recommended_media(weishi_http, body, reason):
    weishi_http["body"] = body
    with pytest.raises(ParseError) as exc:
        await WeiShi().parse_video_id("work42")
    assert exc.value.reason == reason and not weishi_http["probed"]


async def test_short_links_use_native_parser_and_keep_one_user_agent(weishi_http):
    assert parser.detect_source("https://video.weishi.qq.com/short42").value == "weishi"
    info = await parser.parse_video_share_url("https://video.weishi.qq.com/short42")
    assert best_video_format(info).url == _HIGH
    assert len({request.headers["user-agent"] for request in weishi_http["calls"]}) == 1


async def test_invalid_id_and_unrelated_domains_are_not_requested(weishi_http):
    with pytest.raises(ParseError):
        await WeiShi().parse_video_id("work42&feedid=other42")
    with pytest.raises(ParseError):
        await WeiShi().parse_share_url("https://example.com/?id=work42")
    assert not weishi_http["calls"]


async def test_missing_default_url_uses_available_avc_as_preview(weishi_http):
    feed = weishi_http["body"]["rsp_body"]["feeds"][0]
    feed["video_url"] = ""
    info = await WeiShi().parse_video_id("work42")
    assert info.video_url == _LOW and best_video_format(info).url == _HIGH


async def test_original_cover_is_chosen_by_actual_size_over_thumbnail_labels(weishi_http):
    feed = weishi_http["body"]["rsp_body"]["feeds"][0]
    feed["images"] = [{"url": "https://pic.weishi.qq.com/cover.jpg/640", "width": 720, "height": 1280}]
    feed["video_cover"] = {"static_cover": {"url": "https://xp.qpic.cn/cover/480", "width": 1080, "height": 1920}}
    weishi_http["images"] = {
        "https://pic.weishi.qq.com/cover.jpg/640": (640, 1138),
        "https://xp.qpic.cn/cover/480": (480, 853),
        "https://xp.qpic.cn/cover/0": (1080, 1920),
    }
    info = await WeiShi().parse_video_id("work42")
    assert info.cover_url == "https://xp.qpic.cn/cover/0"


async def test_placeholder_original_does_not_replace_a_larger_cover(weishi_http):
    feed = weishi_http["body"]["rsp_body"]["feeds"][0]
    feed["video_cover"] = {"static_cover": {"url": "https://xp.qpic.cn/cover/480"}}
    weishi_http["images"] = {"https://xp.qpic.cn/cover/480": (480, 853), "https://xp.qpic.cn/cover/0": (120, 120)}
    assert (await WeiShi().parse_video_id("work42")).cover_url == "https://xp.qpic.cn/cover/480"


async def test_unverified_cover_keeps_the_originally_provided_url(weishi_http):
    feed = weishi_http["body"]["rsp_body"]["feeds"][0]
    feed["video_cover"] = {"static_cover": {"url": "https://xp.qpic.cn/cover/480"}}
    assert (await WeiShi().parse_video_id("work42")).cover_url == "https://xp.qpic.cn/cover/480"
