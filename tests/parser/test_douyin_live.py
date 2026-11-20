"""抖音直播间：房间识别、各路流的整理、未开播与访客身份失效。接口换成假的，不碰网络。"""

import asyncio
import json
import struct

import httpx
import pytest

from qiuyuantu.parser import VideoSource, detect_source, douyin, douyin_live, parse_video_share_url
from qiuyuantu.parser.douyin import DouYin
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.web import parse as parse_api

WEB_RID = "123456789012"
ROOM_ID = "7300000000000000001"
WEB_URL = f"https://live.douyin.com/{WEB_RID}"
CDN = "http://pull-flv-l1.douyincdn.com/stage/stream-1"
HLS = "http://pull-hls-l1.douyincdn.com/stage/stream-1"

QUALITIES = [
    {"name": "标清", "sdk_key": "ld", "level": 1, "resolution": "480x847"},
    {"name": "超清", "sdk_key": "hd", "level": 3, "resolution": "720x1270"},
    {"name": "蓝光", "sdk_key": "origin", "level": 4, "resolution": "1088x1920"},
]


def _quality(suffix: str, resolution: str, bitrate: int, codec: str = "h264", fps: int = 30) -> dict:
    params = {"VCodec": codec, "vbitrate": bitrate, "resolution": resolution, "fps": fps}
    main = {"flv": f"{CDN}_{suffix}.flv?expire=1", "hls": f"{HLS}_{suffix}.m3u8?expire=1"}
    return {"main": {**main, "sdk_params": json.dumps(params)}}


def _stream_url(**qualities) -> dict:
    data = {
        "ld": _quality("ld", "480x847", 1_000_000),
        "origin": _quality("or4", "1088x1920", 4_000_000),
        "hd": _quality("hd", "720x1270", 2_000_000, codec="h265"),
        # 平台清晰度列表里没有的一档，名称只能按分辨率给
        "md": _quality("md", "240x423", 250_000, fps=15),
        "ao": {"main": {"flv": f"{CDN}.flv?expire=1&only_audio=1", "hls": "", "sdk_params": "{}"}},
        **qualities,
    }
    pull = {"stream_data": json.dumps({"data": data}), "options": {"qualities": QUALITIES}}
    return {"live_core_sdk_data": {"pull_data": pull}}


def _room(**kw) -> dict:
    return {
        "status": 2,
        "title": "今晚聊聊天",
        "cover": {"url_list": ["http://p3-webcast.douyinpic.com/cover.jpeg"]},
        "stream_url": _stream_url(),
        **kw,
    }


OWNER = {"nickname": "主播", "sec_uid": "MS4w", "avatar_thumb": {"url_list": ["https://p3.douyinpic.com/a.jpeg"]}}


def _enter(room: dict | None = None, **kw) -> dict:
    return {"data": {"data": [room or _room()], "user": OWNER}, "status_code": 0, **kw}


def _flv(*tags: tuple[int, bytes]) -> bytes:
    data = b"FLV\x01\x05\x00\x00\x00\x09" + bytes(4)
    for kind, payload in tags:
        data += bytes([kind]) + len(payload).to_bytes(3, "big") + bytes(7) + payload
        data += (11 + len(payload)).to_bytes(4, "big")
    return data


def _client(handler):
    return lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def _meta(**numbers: float) -> bytes:
    body = b"\x02\x00\x0aonMetaData\x08\x00\x00\x00\x02"
    for key, value in numbers.items():
        body += len(key).to_bytes(2, "big") + key.encode() + b"\x00" + struct.pack(">d", value)
    return body + b"\x00\x00\x09"


HEVC_HEAD = _flv(
    (18, _meta(framerate=22, videodatarate=3525, videocodecid=12)),
    (8, b"\xaf\x00" + bytes(4)),
    (9, b"\x1c\x00" + bytes(8)),
)


@pytest.fixture
def site(monkeypatch):
    """假的抖音：首页发访客标识，进房 / 落地页接口按 state 里的内容回答，并记下每次请求。"""
    state = {"enter": [_enter()], "reflow": {"data": {"room": _room(owner={**OWNER, "web_rid": WEB_RID})}}}
    state |= {"requests": [], "head": HEVC_HEAD, "issued": 0}

    async def fetch(self, method, url, *, headers=None, follow_redirects=False, **kw):
        state["requests"].append((method, url, headers or {}, kw.get("params") or {}))
        if url == douyin_live._ENTER:
            body = state["enter"].pop(0) if len(state["enter"]) > 1 else state["enter"][0]
            return httpx.Response(200, content=b"" if body is None else json.dumps(body).encode())
        assert url == douyin_live._REFLOW, url
        return httpx.Response(200, json=state["reflow"])

    async def stream_head(url):
        state["probed"] = url
        if isinstance(state["head"], Exception):
            raise state["head"]
        return state["head"]

    def home(request):
        assert request.method == "HEAD" and str(request.url) == douyin_live._HOME
        state["issued"] += 1
        # 首页对 HEAD 回 404，访客标识仍在响应头里
        return httpx.Response(404, headers={"set-cookie": f"ttwid=guest{state['issued']}; Path=/; HttpOnly"})

    monkeypatch.setattr(DouYin, "fetch", fetch)
    monkeypatch.setattr(douyin_live, "create_async_client", _client(home))
    monkeypatch.setattr(douyin_live, "_stream_head", stream_head)
    monkeypatch.setattr(douyin_live, "_guest", {"ttwid": "", "at": 0.0})
    return state


def _parse(url: str = WEB_URL):
    return asyncio.run(DouYin().parse_share_url(url))


@pytest.mark.parametrize(
    "url, ref",
    [
        (WEB_URL, ("web_rid", WEB_RID)),
        (f"{WEB_URL}?enter_from_merge=link_share&action_type=click", ("web_rid", WEB_RID)),
        (f"https://www.douyin.com/root/live/{WEB_RID}", ("web_rid", WEB_RID)),
        (f"https://www.douyin.com/follow/live/{WEB_RID}", ("web_rid", WEB_RID)),
        ("https://live.douyin.com/some_id.01", ("web_rid", "some_id.01")),
        (f"https://webcast.amemv.com/douyin/webcast/reflow/{ROOM_ID}?u_code=1", ("room_id", ROOM_ID)),
        (f"https://www.iesdouyin.com/share/live/{ROOM_ID}/", ("room_id", ROOM_ID)),
        # 频道页、作品页都不是直播间
        ("https://live.douyin.com/", None),
        ("https://live.douyin.com/categorynew/4_103", None),
        ("https://www.douyin.com/video/7424432820954598707", None),
        ("https://v.douyin.com/abc123/", None),
    ],
)
def test_live_room_links_are_recognised(url, ref):
    assert douyin_live.live_ref(url) == ref


@pytest.mark.parametrize("url", [WEB_URL, f"https://webcast.amemv.com/douyin/webcast/reflow/{ROOM_ID}"])
def test_live_links_belong_to_douyin(url):
    assert detect_source(url) is VideoSource.DouYin


def test_web_room_lists_streams_from_best_to_worst(site):
    info = _parse()

    assert info.content_kind == "live" and not info.video_url and not info.formats
    assert (info.title, info.author.name, info.author.uid) == ("今晚聊聊天", "主播", "MS4w")
    assert info.page_url == WEB_URL
    assert info.cover_url == "https://p3-webcast.douyinpic.com/cover.jpeg"
    assert [(s.label, s.kind) for s in info.streams] == [
        ("蓝光", "video"),
        ("超清", "video"),
        ("标清", "video"),
        ("240p", "video"),
        ("原始推流", "source"),
        ("仅音频", "audio"),
    ]
    best, hd = info.streams[:2]
    assert (best.width, best.height, best.bitrate, best.fps, best.codec) == (1088, 1920, 4_000_000, 30, "H.264")
    assert best.flv_url == "https://pull-flv-l1.douyincdn.com/stage/stream-1_or4.flv?expire=1"
    assert best.hls_url == "https://pull-hls-l1.douyincdn.com/stage/stream-1_or4.m3u8?expire=1"
    assert hd.codec == "H.265"
    assert (info.width, info.height) == (1088, 1920)


def test_original_push_stream_is_the_audio_address_without_the_audio_switch(site):
    source, audio = _parse().streams[-2:]

    assert audio.flv_url == "https://pull-flv-l1.douyincdn.com/stage/stream-1.flv?expire=1&only_audio=1"
    assert source.flv_url == "https://pull-flv-l1.douyincdn.com/stage/stream-1.flv?expire=1"
    assert site["probed"] == source.flv_url
    assert (source.codec, source.fps, source.bitrate) == ("H.265", 22, 3_525_000)
    assert not source.hls_url and not audio.hls_url


@pytest.mark.parametrize(
    "head",
    [
        httpx.ConnectTimeout("timeout"),
        httpx.HTTPStatusError("404", request=httpx.Request("GET", "https://x.test"), response=httpx.Response(404)),
        # 只有声音：这一路不是带画面的原始流，不能列出来
        _flv((8, b"\xaf\x00" + bytes(4))),
        b"<html>",
    ],
)
def test_original_push_stream_is_left_out_unless_it_shows_video(site, head):
    site["head"] = head

    assert [s.kind for s in _parse().streams[-2:]] == ["video", "audio"]


@pytest.mark.parametrize(
    "tags, codec",
    [
        ([(9, b"\x17\x00" + bytes(8))], "H.264"),
        ([(9, b"\x1c\x00" + bytes(8))], "H.265"),
        ([(9, b"\x90hvc1" + bytes(8))], "H.265"),
        ([(9, b"\x90av01" + bytes(8))], "AV1"),
        ([(9, b"\x12\x00" + bytes(8))], ""),
        ([(18, bytes(30)), (8, bytes(6))], None),
    ],
)
def test_flv_video_codec(tags, codec):
    assert douyin_live.flv_video_codec(_flv(*tags)) == codec


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 0.0])
def test_flv_metadata_ignores_values_that_make_no_sense(value):
    assert douyin_live.flv_meta_number(_meta(framerate=value), "framerate") == 0
    assert douyin_live.flv_meta_number(_meta(framerate=30), "width") == 0


def test_enter_request_carries_guest_identity_and_room(site):
    _parse()
    _parse()

    enters = [r for r in site["requests"] if r[1] == douyin_live._ENTER]
    # 访客标识在进程里留着，第二次解析不再去首页要
    assert site["issued"] == 1 and len(enters) == 2
    _, _, headers, params = enters[-1]
    assert headers["Cookie"] == "ttwid=guest1" and "Chrome/130" in headers["User-Agent"]
    assert params["web_rid"] == WEB_RID and params["aid"] == "6383"


def test_stale_guest_identity_is_renewed_once(site):
    # 接口不认访客标识时回的是 200 加空正文
    site["enter"] = [None, _enter()]

    assert _parse().streams
    cookies = [r[2]["Cookie"] for r in site["requests"] if r[1] == douyin_live._ENTER]
    assert cookies == ["ttwid=guest1", "ttwid=guest2"]


def test_empty_answers_are_reported_as_blocked(site):
    site["enter"] = [None]

    with pytest.raises(ParseError) as exc:
        _parse()
    assert exc.value.reason == "blocked"
    assert len([r for r in site["requests"] if r[1] == douyin_live._ENTER]) == 2


def test_missing_guest_identity_is_reported_as_blocked(site, monkeypatch):
    monkeypatch.setattr(douyin_live, "create_async_client", _client(lambda request: httpx.Response(200)))
    with pytest.raises(ParseError) as exc:
        _parse()
    assert exc.value.reason == "blocked"


def test_room_that_is_not_live_says_so(site):
    site["enter"] = [_enter(_room(status=4, stream_url=None))]

    with pytest.raises(ParseError, match="没有在直播（主播：主播）") as exc:
        _parse()
    assert exc.value.reason == "offline"


def test_unknown_room_is_not_found(site):
    site["enter"] = [{"data": {"prompts": "该内容暂时无法查看"}, "status_code": 4001038}]

    with pytest.raises(ParseError, match="没有找到这个直播间") as exc:
        _parse()
    assert exc.value.reason == "deleted"


def test_live_room_without_streams_is_restricted(site):
    site["enter"] = [_enter(_room(stream_url={}))]

    with pytest.raises(ParseError) as exc:
        _parse()
    assert exc.value.reason == "restricted"


def test_rooms_without_quality_details_fall_back_to_the_address_tables(site):
    tables = {
        "flv_pull_url": {"FULL_HD1": f"{CDN}_or4.flv", "HD1": f"{CDN}_hd.flv"},
        "hls_pull_url_map": {"FULL_HD1": f"{HLS}_or4.m3u8"},
    }
    site["enter"] = [_enter(_room(stream_url=tables))]

    streams = _parse().streams
    assert [(s.label, bool(s.flv_url), bool(s.hls_url)) for s in streams] == [
        ("FULL_HD1", True, True),
        ("HD1", True, False),
    ]
    assert streams[0].flv_url.startswith("https://")


def test_app_landing_page_reads_the_room_without_guest_identity(site):
    info = _parse(f"https://webcast.amemv.com/douyin/webcast/reflow/{ROOM_ID}?u_code=1&sec_user_id=MS4w")

    assert len(site["requests"]) == 1 and not site["issued"]
    _, url, headers, params = site["requests"][0]
    assert url == douyin_live._REFLOW and params["room_id"] == ROOM_ID and "Cookie" not in headers
    assert info.page_url == WEB_URL and info.author.name == "主播"
    assert info.streams[0].label == "蓝光"


def test_app_landing_page_of_a_finished_room(site):
    site["reflow"] = {"data": {"room": _room(status=4, stream_url=None, owner=OWNER)}}

    with pytest.raises(ParseError) as exc:
        _parse(f"https://webcast.amemv.com/douyin/webcast/reflow/{ROOM_ID}")
    assert exc.value.reason == "offline"


def test_app_short_link_that_redirects_to_a_live_room(site, monkeypatch):
    def redirect(request):
        target = f"https://webcast.amemv.com/douyin/webcast/reflow/{ROOM_ID}?u_code=1&sec_user_id=MS4w"
        return httpx.Response(302, headers={"Location": target})

    monkeypatch.setattr(douyin, "create_async_client", _client(redirect))
    info = _parse("https://v.douyin.com/abc123/")

    assert info.content_kind == "live" and site["requests"][0][3]["room_id"] == ROOM_ID


def test_live_channel_pages_are_not_rooms():
    with pytest.raises(ParseError, match="直播频道页") as exc:
        _parse("https://live.douyin.com/categorynew/4_103")
    assert exc.value.reason == "unsupported"


def test_live_result_passes_the_empty_check_and_keeps_stream_addresses_out_of_the_proxy(site):
    info = asyncio.run(parse_video_share_url(WEB_URL))
    data = parse_api._client_data(info, WEB_URL)

    assert data["source"] == "douyin" and data["content_kind"] == "live"
    assert data["download_format"] is None
    assert data["streams"][0]["label"] == "蓝光"
    # 直播流只交给用户的播放器：不签名，代理和转换接口也就不接受这些地址
    stream_urls = {s[key] for s in data["streams"] for key in ("flv_url", "hls_url") if s[key]}
    assert stream_urls and not stream_urls & set(data["sig"])
    assert info.cover_url in data["sig"]
