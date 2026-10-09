"""yt-dlp 下载前按大小挑格式：转换用的原视频放不下就降清晰度，用户自选的清晰度放不下就直接报错；
只下到一条分轨（另一条撞上 max_filesize）时不能把它当成品。"""

import pytest

from parse_video_py.convert import fetch
from parse_video_py.convert.fetch import TooLarge

MB = 10**6
SPEC_1080 = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/" + fetch.MERGE_FORMAT


def _fmt(format_id, height=None, size=None, **kw):
    video = height is not None
    return {
        "format_id": format_id,
        "url": f"https://cdn/{format_id}",
        "protocol": "https",
        "ext": "mp4" if video else "m4a",
        "vcodec": "avc1" if video else "none",
        "acodec": "none" if video else "mp4a",
        "height": height,
        "filesize": size,
        **kw,
    }


# 68 分钟的 YouTube 视频（2026-10-08 实测的大小），yt-dlp 给的顺序是从差到好
FORMATS = [
    _fmt("139", size=25 * MB, abr=48),
    _fmt("140", size=66 * MB, abr=128),
    _fmt("134", 360, 174 * MB),
    _fmt("135", 480, 297 * MB),
    _fmt("298", 720, 569 * MB),
    _fmt("299", 1080, 1035 * MB),
]


def _select(formats, spec, limit, fit):
    ctx = {"formats": formats, "has_merged_format": False, "incomplete_formats": False}
    return list(fetch._sized_format(spec, limit, fit)(ctx))


def test_fits_by_stepping_down_resolution():
    [chosen] = _select(FORMATS, SPEC_1080, 300 << 20, fit=True)
    assert chosen["format_id"] == "134+140"


def test_short_video_keeps_requested_resolution():
    [chosen] = _select(FORMATS, SPEC_1080, 10**12, fit=True)
    assert chosen["format_id"] == "299+140"


def test_user_choice_too_large_fails_before_downloading():
    with pytest.raises(TooLarge, match="约 1101 MB.*换低一档"):
        _select(FORMATS, SPEC_1080, 300 << 20, fit=False)


def test_even_lowest_resolution_too_large():
    with pytest.raises(TooLarge, match="视频太长"):
        _select(FORMATS, SPEC_1080, 50 << 20, fit=True)


def test_unknown_size_is_let_through():
    formats = [_fmt("140", size=None), _fmt("299", 1080, None)]
    [chosen] = _select(formats, SPEC_1080, 1, fit=False)
    assert chosen["format_id"] == "299+140"


class _Hooks:
    path = None


def test_merged_file_is_the_result(tmp_path):
    for name in ("s.f140.m4a", "s.mp4"):
        (tmp_path / name).write_bytes(b"x")
    assert fetch._downloaded_file(_Hooks(), {}, tmp_path, "s").name == "s.mp4"


def test_lone_split_track_is_not_a_result(tmp_path):
    """画面那条撞上 max_filesize 停在 .part，剩下的音轨不能拿去当原视频。"""
    for name in ("s.f140.m4a", "s.f399.mp4.part"):
        (tmp_path / name).write_bytes(b"x")
    info = {"requested_downloads": [{"filepath": str(tmp_path / "s.mp4")}]}
    with pytest.raises(TooLarge, match="上限"):
        fetch._downloaded_file(_Hooks(), info, tmp_path, "s")
    assert not list(tmp_path.iterdir())
