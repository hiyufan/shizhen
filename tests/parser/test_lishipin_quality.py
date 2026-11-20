"""梨视频的字段名和返回顺序不能代替实际画质比较。"""

import httpx
import pytest

from qiuyuantu.parser import base, mp4_probe
from qiuyuantu.parser.base import FormatInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.lishipin import LiShiPin
from qiuyuantu.parser.quality import best_video_format


@pytest.fixture
def pear_http(monkeypatch):
    state = {"body": {}, "calls": [], "probes": {}}

    def reply(request):
        state["calls"].append(request)
        return httpx.Response(200, json=state["body"])

    async def probe(url, headers):
        assert headers["Referer"] == "https://www.pearvideo.com/detail_42"
        return state["probes"].get(url)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(mp4_probe, "probe", probe)
    return state


def _body(videos):
    return {"resultCode": "1", "systemTime": "1700000000", "videoInfo": {"playSta": "1", "videos": videos}}


async def test_highest_actual_track_wins_over_src_name_and_order(pear_http):
    source = "https://video.pearvideo.com/mp4/cont-42-1-hd.mp4"
    high = "https://video.pearvideo.com/mp4/cont-42-2.mp4"
    pear_http["body"] = _body({"srcUrl": source, "hdUrl": high, "sdUrl": "", "hdflvUrl": "live.flv"})
    pear_http["probes"] = {
        source: FormatInfo(url=source, width=720, height=406, fps=30),
        high: FormatInfo(url=high, width=1920, height=1080, fps=60),
    }
    info = await LiShiPin().parse_video_id("42")
    assert info.video_url == high and (info.width, info.height, info.video_fps) == (1920, 1080, 60)
    assert [f.url for f in info.formats] == [source]
    assert best_video_format(info).url == high
    assert info.video_headers["User-Agent"] == pear_http["calls"][0].headers["user-agent"]


async def test_unknown_dimensions_keep_source_without_inventing_hd_or_4k(pear_http):
    pear_http["body"] = _body({"srcUrl": "https://video.pearvideo.com/1700000000-2-hd.mp4"})
    info = await LiShiPin().parse_video_id("42")
    assert info.video_url.endswith("/cont-42-2-hd.mp4")
    assert not info.width and not info.height and not best_video_format(info).label


async def test_timestamp_repair_does_not_change_directories_or_signatures(pear_http):
    pear_http["body"] = _body({"srcUrl": "//video.pearvideo.com/1700000000/1700000000-2.mp4?signature=1700000000"})
    info = await LiShiPin().parse_video_id("42")
    assert info.video_url == "https://video.pearvideo.com/1700000000/cont-42-2.mp4?signature=1700000000"


async def test_different_work_is_rejected_before_probing(pear_http):
    pear_http["body"] = _body({"srcUrl": "https://video.pearvideo.com/cont-43-2.mp4"})
    with pytest.raises(ParseError, match="作品编号"):
        await LiShiPin().parse_video_id("42")


@pytest.mark.parametrize("body", [{"resultCode": "4"}, _body({}) | {"videoInfo": {"playSta": "0"}}])
async def test_unavailable_playback_has_a_meaningful_error(pear_http, body):
    pear_http["body"] = body
    with pytest.raises(ParseError) as exc:
        await LiShiPin().parse_video_id("42")
    assert exc.value.reason == "restricted"


async def test_no_video_is_empty_and_invalid_ids_never_make_requests(pear_http):
    pear_http["body"] = _body({"srcUrl": "", "hdUrl": ""})
    with pytest.raises(ParseError) as exc:
        await LiShiPin().parse_video_id("42")
    assert exc.value.reason == "empty"
    pear_http["calls"].clear()
    with pytest.raises(ParseError) as exc:
        await LiShiPin().parse_video_id("42&contId=43")
    assert exc.value.reason == "unsupported" and not pear_http["calls"]
