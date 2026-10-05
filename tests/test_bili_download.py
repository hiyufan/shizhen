"""B 站高清 / 仅音频下载不经 yt-dlp（它要打开网页，海外机房 IP 一律 412），按 format_spec 自己挑分轨。"""

import pytest

from parse_video_py.convert import bili
from parse_video_py.convert.fetch import TooLarge


def _v(height, codecid, bw):
    return {
        "height": height,
        "width": height * 16 // 9,
        "codecid": codecid,
        "bandwidth": bw,
        "baseUrl": f"v{height}-{codecid}",
    }


DASH = {
    "video": [_v(1080, 7, 3_000_000), _v(1080, 12, 1_400_000), _v(720, 7, 1_300_000), _v(720, 12, 700_000)],
    "audio": [{"bandwidth": 60_000, "baseUrl": "a1"}, {"bandwidth": 85_000, "baseUrl": "a2"}],
}
SPEC_1080 = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]"
BIG = 10**12


def test_prefers_h264_at_requested_height():
    video, audio = bili.pick(DASH, SPEC_1080, 200, BIG)
    assert (video["height"], video["codecid"], audio["baseUrl"]) == (1080, 7, "a2")


def test_caps_height():
    video, _ = bili.pick(DASH, "bv*[height<=720]+ba/b", 200, BIG)
    assert video["height"] == 720


def test_falls_back_to_h265_when_h264_is_over_the_limit():
    seconds = 600  # H.264 约 231 MB，H.265 约 111 MB
    video, _ = bili.pick(DASH, SPEC_1080, seconds, 150 * 10**6)
    assert video["codecid"] == 12


def test_too_large_says_how_big():
    with pytest.raises(TooLarge, match="约 988 MB"):
        bili.pick(DASH, SPEC_1080, 5323, 300 << 20)


def test_audio_only():
    video, audio = bili.pick(DASH, "ba[ext=m4a]/ba", 200, BIG)
    assert video is None and audio["bandwidth"] == 85_000


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://www.bilibili.com/video/BV1GJ411x7h7", True),
        ("https://b23.tv/HnGGRpw", True),
        ("https://m.bilibili.com/video/BV1GJ411x7h7", True),
        ("https://www.youtube.com/watch?v=x", False),
        ("https://notbilibili.com/video/x", False),
    ],
)
def test_handles(url, ok):
    assert bili.handles(url) is ok
