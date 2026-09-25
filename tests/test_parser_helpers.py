"""解析器里不碰网络的纯函数。"""

import json

from parse_video_py.parser.bilibili import dash_formats, guest_params, id_param
from parse_video_py.parser.douyin import _looks_like_note


def test_av_ids_use_numeric_params():
    assert id_param("BV1GJ411x7h7", "aid") == "bvid=BV1GJ411x7h7"
    assert id_param("av170001", "aid") == "aid=170001"
    assert id_param("AV2", "avid") == "avid=2"
    # 不是纯数字的别误判
    assert id_param("avatar", "aid") == "bvid=avatar"


def test_dash_formats_only_above_progressive_height():
    play = {"dash": {
        "audio": [{"bandwidth": 64_000}, {"bandwidth": 192_000}],
        "video": [
            {"width": 1920, "height": 1080, "bandwidth": 2_000_000},
            {"width": 1920, "height": 1080, "bandwidth": 3_000_000},
            {"width": 1280, "height": 720, "bandwidth": 1_000_000},
        ],
    }}
    fmts = dash_formats(play, duration=100, above=720)
    assert [f.label for f in fmts] == ["1080p", "仅音频"]
    # 同一高度取码率最高的那条，体积 = (视频 + 最好的音频) × 时长
    assert fmts[0].filesize == (3_000_000 + 192_000) * 100 // 8
    assert "height<=1080" in fmts[0].format_spec


def test_vertical_video_labels_by_short_side_but_filters_by_height():
    play = {"dash": {"audio": [], "video": [{"width": 1080, "height": 1920, "bandwidth": 1}]}}
    (fmt,) = dash_formats(play, duration=10, above=720)
    assert fmt.label == "1080p"
    assert "height<=1920" in fmt.format_spec


def test_guest_params_shape():
    p = guest_params()
    assert p["try_look"] == 1
    inter = json.loads(p["dm_img_inter"])
    assert set(inter) == {"ds", "wh", "of"}
    assert " " not in p["dm_img_inter"], "B 站要紧凑 JSON"


def test_douyin_note_urls():
    assert _looks_like_note("https://www.douyin.com/note/7424432820954598707")
    assert _looks_like_note("https://www.iesdouyin.com/share/note/7424432820954598707/?from=web")
    assert _looks_like_note("https://www.iesdouyin.com/share/slides/7424432820954598707/")
    assert not _looks_like_note("https://www.iesdouyin.com/share/video/7424432820954598707/")
