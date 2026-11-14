"""QQ 音乐封面来源与档位回退；真实响应去掉限时地址后离线回放。"""

import json
from pathlib import Path

import pytest

from qiuyuantu.parser import qqmusic_artwork as artwork
from qiuyuantu.parser.music import QQMusic

URL = "https://v0.stream.tencentmusic.com/cover.mp4?fixture=1"
SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "qqmusic_artwork.json").read_text())["albums"]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["name"])
async def test_public_album_responses_select_the_best_available_cover(monkeypatch, sample):
    calls = []

    async def request(_parser, module, method, param):
        calls.append(method)
        if method == "GetAlbumDetail":
            assert module == "music.musichallAlbum.AlbumInfoServer"
            assert param == {"albumMid": sample["album_mid"]}
            return {"basicInfo": sample["basicInfo"]}
        assert module == "music.stream.MvUrlProxy" and method == "GetMvUrls"
        assert set(param["vids"]) == set(sample["videos"])
        return sample["videos"]

    monkeypatch.setattr(artwork, "_request", request)
    cover = f"https://y.gtimg.cn/music/photo_new/T002M000{sample['album_mid']}.jpg"
    expected = (
        f"https://v0.stream.tencentmusic.com/{sample['expected_vid']}.{sample['expected_filetype']}.mp4?fixture=1"
    )
    assert await artwork.dynamic_cover(QQMusic(), cover) == expected
    assert calls == ["GetAlbumDetail", "GetMvUrls"]


@pytest.mark.parametrize("key", ["freeflow_url", "url", "comm_url", "validUrl"])
@pytest.mark.parametrize("scalar", [False, True])
def test_all_gateway_address_fields_are_validated_before_selection(key, scalar):
    value = (
        URL if scalar else ["https://example.com/not-a-cover.mp4", "https://v0.stream.tencentmusic.com:bad/x.mp4", URL]
    )
    data = {"vid": {"mp4": [{"code": 0, "filetype": 40, key: value}]}}
    assert artwork._best_video(data, "vid") == URL


@pytest.mark.parametrize("bad", [None, [], {}, "https://example.com/cover.mp4"])
def test_unusable_primary_address_field_keeps_same_quality_backups(bad):
    data = {"vid": {"mp4": [{"code": 0, "filetype": 40, "freeflow_url": bad, "url": [URL]}]}}
    assert artwork._best_video(data, "vid") == URL


def test_bad_quality_rows_do_not_hide_a_valid_lower_quality():
    files = [
        None,
        [],
        {"code": 0, "filetype": "broken", "freeflow_url": [URL + "&bad=1"]},
        {"code": 0, "filetype": [], "freeflow_url": [URL + "&bad=2"]},
        {"code": 0, "filetype": "40", "freeflow_url": ["https://example.com/cover.mp4"]},
        {"code": 2000, "filetype": 30, "url": [URL + "&unavailable=1"]},
        {"code": 0, "filetype": 20, "url": [URL]},
    ]
    assert artwork._best_video({"vid": {"mp4": files}}, "vid") == URL


@pytest.mark.parametrize("detail", [[], "invalid", {"mp4": {}}, {"mp4": "invalid"}])
async def test_bad_first_video_does_not_hide_the_next_cover(monkeypatch, detail):
    async def request(_parser, _module, method, _param):
        if method == "GetAlbumDetail":
            return {"basicInfo": {"dynamicCoverVid": "primary", "headVideoVid": "head"}}
        return {
            "primary": detail,
            "head": {"duration": 6, "mp4": [{"code": 0, "videoType": 8, "filetype": 20, "url": [URL]}]},
        }

    monkeypatch.setattr(artwork, "_request", request)
    assert await artwork.dynamic_cover(QQMusic(), "https://y.gtimg.cn/music/photo_new/T002M000albumMID.jpg") == URL


@pytest.mark.parametrize("primary", [[], {}, 123, "../invalid"])
def test_bad_primary_vid_and_duplicate_placeholders_do_not_hide_header_list(primary):
    basic = {
        "dynamicCoverVid": primary,
        "headVideoVid": "head",
        "headMediaList": [{"vid": "head"}, {}, {"vid": "../invalid"}, None, {"vid": "extra"}],
    }
    assert artwork._cover_candidates(basic) == [("head", True), ("extra", True)]


def test_header_list_scan_and_video_lookup_are_bounded():
    basic = {"headMediaList": [{}] * 32 + [{"vid": "tooLate"}]}
    assert artwork._cover_candidates(basic) == []
    basic["headMediaList"] = [{"vid": f"vid{i}"} for i in range(32)]
    assert artwork._cover_candidates(basic) == [(f"vid{i}", True) for i in range(4)]


@pytest.mark.parametrize("extension", ["jpg", "jpeg", "png", "webp", "avif"])
async def test_album_lookup_does_not_depend_on_static_image_encoding(monkeypatch, extension):
    async def request(_parser, _module, method, param):
        assert method == "GetAlbumDetail" and param == {"albumMid": "albumMID"}
        return {"basicInfo": {}}

    monkeypatch.setattr(artwork, "_request", request)
    assert (
        await artwork.dynamic_cover(QQMusic(), f"https://y.gtimg.cn/music/photo_new/T002M000albumMID_2.{extension}")
        == ""
    )


def test_3d_pages_and_album_mvs_are_not_downloadable_cover_videos():
    assert artwork._cover_candidates({"threeDUrl": URL, "vid": "albumMv", "bookletUrl": URL}) == []


@pytest.mark.parametrize("key", ["url", "comm_url", "validUrl"])
def test_backups_cannot_bypass_header_artwork_type_check(key):
    data = {"head": {"duration": 6, "mp4": [{"code": 0, "videoType": 0, "filetype": 40, key: [URL]}]}}
    assert artwork._best_video(data, "head", artwork_only=True) == ""
