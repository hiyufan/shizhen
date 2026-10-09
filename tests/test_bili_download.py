"""B 站高清 / 仅音频下载不经 yt-dlp（它要打开网页，海外机房 IP 一律 412），按 format_spec 自己挑分轨。"""

import asyncio
from pathlib import Path

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


def test_fit_steps_down_to_a_lower_height():
    """转换用的原视频：1080p 两种编码都超了，就拿 720p。"""
    seconds = 1500  # 1080p H.265 约 278 MB，720p H.264 约 260 MB
    video, _ = bili.pick(DASH, SPEC_1080, seconds, 270 * 10**6, fit=True)
    assert (video["height"], video["codecid"]) == (720, 7)


def test_fit_still_fails_when_nothing_fits():
    with pytest.raises(TooLarge, match="视频太长：最低清晰度也约 147 MB"):
        bili.pick(DASH, SPEC_1080, 1500, 100 * 10**6, fit=True)


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


async def test_video_and_audio_tracks_download_together(monkeypatch, tmp_path):
    """画面和声音一起下（以前先下完画面再下声音），进度按两条的合计算。"""
    running, peak, reports, merged = [0], [0], [], []

    async def fake_download(url, dest, headers, on_bytes):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0.02)
        dest.write_bytes(b"x" * 10)
        on_bytes(10, 10)
        running[0] -= 1

    async def fake_ffmpeg(args, *a, **k):
        merged.append(args)
        Path(args[-1]).write_bytes(b"mp4")

    parser = bili.BiliBili

    async def noop(self):
        return None

    async def bvid(self, url):
        return "BV1"

    async def cid(self, bvid):
        return 1

    async def play(self, bvid, cid):
        return {"data": {"timelength": 10_000, "dash": DASH}}, {}

    monkeypatch.setattr(parser, "_ensure_buvid", noop)
    monkeypatch.setattr(parser, "_get_bvid_from_url", bvid)
    monkeypatch.setattr(parser, "_page_cid", cid)
    monkeypatch.setattr(parser, "_play_urls", play)
    monkeypatch.setattr(bili, "download_source", fake_download)
    monkeypatch.setattr(bili.ffmpeg, "run", fake_ffmpeg)

    out = await bili.download(
        "https://www.bilibili.com/video/BV1", SPEC_1080, tmp_path, "s", lambda p, m: reports.append(p)
    )
    assert peak[0] == 2 and out.name == "s.mp4" and len(merged) == 1
    assert reports == sorted(reports)  # 两条一起报，进度也不会往回跳
    assert not list(tmp_path.glob("*.m4s"))  # 分轨用完就删
