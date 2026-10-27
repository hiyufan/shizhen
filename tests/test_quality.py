"""最高下载档位和浏览器预览独立；来源未提供的原文件不能由排序推断。"""

import json

import pytest

from qiuyuantu.parser.acfun import AcFun
from qiuyuantu.parser.base import FormatInfo, VideoInfo
from qiuyuantu.parser.instagram import to_video_info
from qiuyuantu.parser.qqvideo import _format as qq_format
from qiuyuantu.parser.quality import apply_video_formats, best_video_format, direct_format, number
from qiuyuantu.parser.redbook import _video_tracks
from qiuyuantu.parser.twitter import _original_image
from qiuyuantu.parser.weibo import _largest_pic
from qiuyuantu.parser.ytdlp import YtDlp
from qiuyuantu.web.parse import _client_data


@pytest.mark.parametrize("value", [None, "", "unknown", "nan", "inf", -5])
def test_invalid_quality_metadata_cannot_win(value):
    assert number(value) == 0


def test_default_download_uses_highest_track_without_replacing_preview():
    best = FormatInfo(url="https://cdn.test/4k", height=2160, fps=60)
    info = VideoInfo("https://cdn.test/preview", "", width=1280, height=720, formats=[best])
    data = _client_data(info, "https://example.com/post")
    assert data["video_url"] == info.video_url
    assert data["download_format"]["url"] == best.url
    assert best.url in data["sig"]


def test_server_merged_highest_download_retains_exact_format():
    high = FormatInfo(height=4320, format_spec="bv[height=4320]+ba/b[height=4320]")
    info = VideoInfo("https://cdn.test/720.mp4", "", height=720, formats=[high, FormatInfo(label="仅音频")])
    assert best_video_format(info) == high


def test_short_side_and_fps_rank_portrait_and_landscape_correctly():
    info = VideoInfo("preview", "", width=1080, height=1920, video_fps=30)
    info.formats = [
        FormatInfo(url="720-portrait", width=720, height=1280, fps=60),
        FormatInfo(url="1080-60", width=1920, height=1080, fps=60),
    ]
    assert best_video_format(info).url == "1080-60"


def test_download_does_not_infer_quality_from_codec_or_filesize():
    info = VideoInfo("preview", "", height=1080, video_fps=60)
    info.formats = [FormatInfo(url="h265", height=1080, fps=30, codec="H.265", filesize=10**9)]
    assert best_video_format(info).url == "preview"


def test_original_webm_asset_keeps_its_extension_in_default_download():
    info = VideoInfo("https://cdn.example.test/wallpaper.webm?source=steam", "", source="steam")
    assert best_video_format(info).ext == "webm"


def test_unordered_direct_tracks_keep_highest_resolution_and_framerate():
    rows = [
        {"url": "720", "width": 1280, "height": 720, "bitrate": 9000000},
        {"url": "1080-30", "width": 1920, "height": 1080, "fps": "30"},
        {"url": "1080-60", "width": 1920, "height": 1080, "fps": "60"},
    ]
    info = apply_video_formats(VideoInfo("", ""), [direct_format(row["url"], row) for row in rows])
    assert info.video_url == "1080-60"
    assert {f.url for f in info.formats} == {"720", "1080-30"}


def test_manifest_only_highest_track_is_merged_instead_of_saved_as_text():
    fmt = direct_format("https://cdn.example.test/playlist.m3u8", {"width": 3840, "height": 2160})
    info = apply_video_formats(VideoInfo("", ""), [fmt])
    assert not info.video_url
    best = best_video_format(info)
    assert not best.url and "[height=2160]" in best.format_spec


def test_qq_segmented_video_is_not_delivered_as_just_its_first_mp4_clip():
    data = {"vl": {"vi": [{"vw": 1920, "vh": 1080, "cl": {"fc": 8}}]}}
    fmt = qq_format(data)
    assert not fmt.url
    assert "[height=1080]" in fmt.format_spec


def test_ytdlp_lists_real_8k_and_nonstandard_resolutions_without_fake_tiers():
    formats = [
        {"url": str(height), "height": height, "vcodec": "av01", "acodec": "none"} for height in (4320, 1440, 900)
    ]
    info = YtDlp._to_video_info({"formats": formats}, "https://example.com/video")
    assert [f.height for f in info.formats] == [4320, 1440, 900]
    assert best_video_format(info).height == 4320
    assert info.formats[0].format_spec == "bv[height=4320]+ba/b[height=4320]/bv[height=4320]"


def test_ytdlp_does_not_choose_lower_mp4_over_higher_webm():
    formats = [
        {"url": "low", "height": 360, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"},
        {"url": "high", "height": 1080, "ext": "webm", "vcodec": "vp09", "acodec": "opus"},
    ]
    assert YtDlp._to_video_info({"formats": formats}, "https://example.com/video").video_url == "high"


def test_ytdlp_retains_same_resolution_higher_framerate_download():
    formats = [
        {"url": "preview", "height": 1080, "fps": 30, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"},
        {"url": "60fps", "height": 1080, "fps": 60, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
    ]
    info = YtDlp._to_video_info({"formats": formats}, "https://example.com/video")
    assert info.video_url == "preview"
    assert best_video_format(info).fps == 60


async def test_acfun_current_page_keeps_1080p60_without_downloading_manifest_as_mp4(monkeypatch):
    representations = [
        {"url": "low.m3u8", "width": 720, "height": 1280, "frameRate": 30, "avgBitrate": 2000},
        {"url": "high.m3u8", "width": 1080, "height": 1920, "frameRate": 59.99, "avgBitrate": 6000},
    ]
    video = {
        "title": "AcFun",
        "coverUrl": "cover.jpg",
        "currentVideoInfo": {"ksPlayJson": json.dumps({"adaptationSet": [{"representation": representations}]})},
    }

    async def page(_self, _url, **kwargs):
        assert kwargs["headers"]["Referer"] == "https://www.acfun.cn/"
        return "window.videoInfo = " + json.dumps(video) + ";"

    monkeypatch.setattr(AcFun, "get_text", page)
    info = await AcFun().parse_share_url("https://www.acfun.cn/v/ac123")
    best = best_video_format(info)
    assert best.label == "1080p60" and best.bitrate == 6000000
    assert not best.url and "[height=1920][fps>45]" in best.format_spec


def test_instagram_uses_largest_valid_picture_regardless_of_order():
    item = {
        "media_type": 1,
        "image_versions2": {
            "candidates": [
                {"url": "preview", "width": 640, "height": 640},
                {"width": 10000, "height": 10000},
                {"url": "original", "width": 3072, "height": 4096},
            ]
        },
    }
    assert to_video_info(item, "code").images[0].url == "original"


def test_weibo_prefers_explicit_original_over_large_preview():
    assert _largest_pic({"large": {"url": "large"}, "original": {"url": "original"}}) == "original"
    assert _largest_pic({"url": "fallback"}) == "fallback"


def test_twitter_original_size_keeps_format_and_unrelated_parameters():
    assert _original_image("https://pbs.twimg.com/media/abc?format=png&name=small") == (
        "https://pbs.twimg.com/media/abc?format=png&name=orig"
    )
    assert _original_image("https://other.test/media/abc.jpg") == "https://other.test/media/abc.jpg"


def test_redbook_h265_order_does_not_hide_4k_track():
    data = {
        "media": {
            "stream": {
                "h264": [{"masterUrl": "preview", "width": 1920, "height": 1080}],
                "h265": [
                    {"masterUrl": "low", "width": 1280, "height": 720},
                    {"masterUrl": "4k", "width": 3840, "height": 2160},
                ],
            }
        }
    }
    tracks = _video_tracks(data)
    assert tracks.url == "preview"
    assert tracks.formats[0].url == "4k"
