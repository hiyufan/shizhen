"""百度多档顺序不固定，标签与实际文件也可能不一致。"""

import httpx
import pytest

from qiuyuantu.parser import base, mp4_probe
from qiuyuantu.parser.base import FormatInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.haokan import HaoKan
from qiuyuantu.parser.quality import best_video_format, verify_direct_formats
from qiuyuantu.parser.quanmin import QuanMin


@pytest.fixture
def network(monkeypatch):
    routes = {}

    def reply(request):
        return httpx.Response(200, json=routes[str(request.url)])

    monkeypatch.setattr(
        base, "create_async_client", lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(reply), **kwargs)
    )

    async def probe(*_args):
        return None

    monkeypatch.setattr(mp4_probe, "probe", probe)
    return routes


def _meta(rows):
    return {
        "id": "42",
        "playurl": "https://cdn.test/360.mp4",
        "poster": "cover.jpg",
        "title": "title",
        "duration": 10,
        "clarityUrl": rows,
        "mth": {"mthid": "user", "author_name": "name", "author_photo": "avatar.jpg"},
    }


async def test_haokan_selects_actual_4k_instead_of_default_or_last_entry(network):
    meta = _meta(
        [
            {"key": "4k", "title": "4K", "vodVideoHW": "2160$$3840", "url": "https://cdn.test/4k.mp4"},
            {"key": "sd", "vodVideoHW": "360$$640", "url": "https://cdn.test/360.mp4"},
            {"key": "sc", "vodVideoHW": "720$$1280", "url": "https://cdn.test/720.mp4"},
        ]
    )
    meta["videoInfoExt"] = {"4k": {"vodVideoSize": 50000000, "vodVideoBps": 40000000}}
    network["https://haokan.baidu.com/v?_format=json&vid=42"] = {
        "errno": 0,
        "data": {"apiData": {"curVideoMeta": meta}},
    }
    info = await HaoKan().parse_video_id("42")
    best = best_video_format(info)
    assert (best.width, best.height, best.label) == (3840, 2160, "2160p")
    assert info.duration == 10 and len(info.formats) == 2


async def test_probe_overrides_a_mislabeled_4k_track_with_actual_720p(network, monkeypatch):
    meta = _meta(
        [
            {"title": "4K", "vodVideoHW": "2160$$3840", "url": "https://cdn.test/not-4k.mp4"},
            {"vodVideoHW": "1080$$1920", "url": "https://cdn.test/1080.mp4"},
        ]
    )
    network["https://haokan.baidu.com/v?_format=json&vid=42"] = {
        "errno": 0,
        "data": {"apiData": {"curVideoMeta": meta}},
    }

    async def probe(url, _headers):
        if "not-4k" in url:
            return FormatInfo(url=url, width=1280, height=720, fps=30, label="720p")
        return FormatInfo(url=url, width=1920, height=1080, fps=60, label="1080p60")

    monkeypatch.setattr(mp4_probe, "probe", probe)
    info = await HaoKan().parse_video_id("42")
    assert info.video_url.endswith("1080.mp4") and info.video_fps == 60


async def test_api_returning_a_different_work_is_rejected(network):
    network["https://haokan.baidu.com/v?_format=json&vid=43"] = {
        "errno": 0,
        "data": {"apiData": {"curVideoMeta": _meta([])}},
    }
    with pytest.raises(ParseError, match="作品编号"):
        await HaoKan().parse_video_id("43")


async def test_quanmin_does_not_assume_second_entry_is_the_highest(network):
    data = {
        "meta": {
            "statusText": "",
            "title": "title",
            "image": "cover.jpg",
            "video_info": {
                "clarityUrl": [
                    {"url": "https://cdn.test/1080.mp4", "height": 1080, "width": 1920},
                    {"url": "https://cdn.test/360.mp4", "height": 360, "width": 640},
                ]
            },
        },
        "author": {"id": "id", "name": "name", "icon": "avatar"},
    }
    network[
        "https://quanmin.hao222.com/wise/growth/api/sv/immerse?source=share-h5&pd=qm_share_mvideo&_format=json&vid=42"
    ] = {"errno": 0, "data": data}
    info = await QuanMin().parse_video_id("42")
    assert info.video_url.endswith("1080.mp4") and info.formats[0].height == 360


async def test_unbounded_quality_list_is_rejected_before_network():
    with pytest.raises(ParseError, match="数量"):
        await verify_direct_formats([FormatInfo(url=f"https://cdn.test/{index}.mp4") for index in range(33)])
