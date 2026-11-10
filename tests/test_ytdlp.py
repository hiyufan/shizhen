"""yt-dlp 的 info dict -> VideoInfo：直链挑哪一路、哪些清晰度要服务端合并。"""

from qiuyuantu.parser.ytdlp import YtDlp


def _fmt(**kw):
    base = {"url": f"https://cdn/{kw.get('format_id', 'x')}", "protocol": "https", "vcodec": "none", "acodec": "none"}
    return {**base, **kw}


_HEADERS = {
    "User-Agent": "UA",
    "Accept": "text/html",
    "Sec-Fetch-Mode": "navigate",
    "Referer": "https://www.tiktok.com/@a",
}
# yt-dlp 给的是 Set-Cookie 的写法，带 = + 的值加了引号（原样发出去 TikTok 不认）
_COOKIES = (
    'ttwid=1%7C2; Domain=.tiktok.com; Path=/; Secure; Expires=1823445095; tt_chain_token="sA+x=="; Domain=.tiktok.com'
)

INFO = {
    "title": "t",
    "extractor_key": "Youtube",
    "uploader": "u",
    "uploader_id": "uid",
    "webpage_url": "https://www.youtube.com/watch?v=1",
    "duration": 12,
    "thumbnails": [{"url": "small.jpg"}, {"url": "big.jpg"}],
    "formats": [
        _fmt(format_id="18", ext="mp4", vcodec="avc1", acodec="mp4a", height=360, width=640),
        _fmt(
            format_id="22",
            ext="mp4",
            vcodec="avc1",
            acodec="mp4a",
            height=720,
            width=1280,
            http_headers=_HEADERS,
            cookies=_COOKIES,
        ),
        _fmt(format_id="hls", ext="mp4", vcodec="avc1", acodec="mp4a", height=1080, protocol="m3u8_native"),
        _fmt(format_id="137", ext="mp4", vcodec="avc1", height=1080, filesize=50),
        _fmt(format_id="401", ext="mp4", vcodec="av01", height=2160, filesize_approx=900),
        _fmt(format_id="140", ext="m4a", acodec="mp4a"),
    ],
}


def test_direct_link_is_best_progressive_http_stream():
    info = YtDlp._to_video_info(INFO, "https://youtu.be/1")
    assert info.video_url == "https://cdn/22" and info.video_headers == {
        "User-Agent": "UA",
        "Referer": "https://www.tiktok.com/@a",
        "Cookie": "ttwid=1%7C2; tt_chain_token=sA+x==",  # TikTok 缺了 cookie 一律 403
    }
    assert (info.width, info.height, info.duration) == (1280, 720, 12.0)
    assert (info.source, info.cover_url, info.page_url) == ("youtube", "big.jpg", "https://www.youtube.com/watch?v=1")


def test_higher_resolutions_need_server_merge():
    info = YtDlp._to_video_info(INFO, "https://youtu.be/1")
    assert [(f.label, f.filesize) for f in info.formats] == [("2160p", 900), ("1080p", 50), ("仅音频", 0)]
    assert all(not f.url and f.format_spec for f in info.formats)


def test_no_progressive_stream_falls_back_to_top_level_url():
    info = YtDlp._to_video_info({"url": "https://cdn/v.mp4", "ext": "mp4", "width": 2, "height": 4}, "https://x/1")
    assert (info.video_url, info.width, info.height, info.source) == ("https://cdn/v.mp4", 2, 4, "ytdlp")


def test_storyboard_and_jpeg_formats_cannot_become_the_highest_video():
    formats = [
        _fmt(ext="mhtml", vcodec="images", height=8000),
        _fmt(ext="jpg", vcodec="mjpeg", height=9000),
        _fmt(ext="mp4", vcodec="avc1", acodec="mp4a", height=720),
    ]
    info = YtDlp._to_video_info({"formats": formats}, "https://example.com/video")
    assert info.height == 720
    assert info.formats == []


# YouTube 竖屏 1080p 同时有 AV1 / VP9 / H.264 三路画面、Opus / AAC 两路声音（2026-11 实际的格式表）
_SHORTS = [
    _fmt(format_id="18", ext="mp4", vcodec="avc1.42001E", acodec="mp4a.40.2", height=640, width=360, tbr=600),
    _fmt(format_id="399", ext="mp4", vcodec="av01.0.08M.08", height=1920, width=1080, fps=30, tbr=2400),
    _fmt(format_id="248", ext="webm", vcodec="vp9", height=1920, width=1080, fps=30, tbr=3000),
    _fmt(format_id="137", ext="mp4", vcodec="avc1.640028", height=1920, width=1080, fps=30, tbr=4300),
    _fmt(format_id="140", ext="m4a", acodec="mp4a.40.2", abr=129),
    _fmt(format_id="251", ext="webm", acodec="opus", abr=140),
]


def _selected(spec, formats):
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "merge_output_format": "mp4"}) as ydl:
        info = {"formats": [{**f, "format": f["format_id"]} for f in formats]}
        ydl.sort_formats(info)  # 真实下载时 yt-dlp 先按自己的规则排好，选择器取排在最后的
        chosen = next(ydl.build_format_selector(spec)({**info, "incomplete_formats": False}))
    return [f["format_id"] for f in chosen.get("requested_formats") or [chosen]]


def test_photos_compatible_h264_and_aac_are_merged_for_the_listed_tier():
    """yt-dlp 默认会挑 AV1 + Opus，合成的 MP4 iPhone 相册存不进去；列出的档位要和实际下载的编码一致。"""
    info = YtDlp._to_video_info({"formats": _SHORTS}, "https://www.youtube.com/shorts/1")
    tier = info.formats[0]
    assert (tier.label, tier.codec) == ("1080p", "avc1.640028")
    assert _selected(tier.format_spec, _SHORTS) == ["137", "140"]


def test_tiers_up_to_1080p_are_h264_when_the_source_has_it():
    """和 HLS 兜底一样：1080p 以内宁可少 60 帧，也不列多数设备放不了的 VP9 / AV1 档位。"""
    formats = [
        *_SHORTS,
        _fmt(format_id="303", ext="webm", vcodec="vp9", height=1920, width=1080, fps=60, tbr=4000),
        _fmt(format_id="397", ext="mp4", vcodec="av01.0.05M.08", height=1080, width=608, fps=30, tbr=900),
    ]
    tiers = YtDlp._to_video_info({"formats": formats}, "https://www.youtube.com/shorts/1").formats
    assert [(f.label, f.codec[:4]) for f in tiers] == [("1080p", "avc1"), ("仅音频", "")]
    assert _selected(tiers[0].format_spec, formats) == ["137", "140"]


def test_2k_and_above_keep_framerate_and_download_the_labelled_codec():
    formats = [
        _fmt(format_id="18", ext="mp4", vcodec="avc1.42001E", acodec="mp4a.40.2", height=360, width=640, tbr=600),
        _fmt(format_id="308", ext="webm", vcodec="vp9", height=1440, width=2560, fps=60, tbr=13000),
        _fmt(format_id="400", ext="mp4", vcodec="av01.0.12M.08", height=1440, width=2560, fps=60, tbr=11000),
        _fmt(format_id="271", ext="webm", vcodec="vp9", height=1440, width=2560, fps=30, tbr=9000),
        _fmt(format_id="140", ext="m4a", acodec="mp4a.40.2", abr=129),
        _fmt(format_id="251", ext="webm", acodec="opus", abr=140),
    ]
    tier = YtDlp._to_video_info({"formats": formats}, "https://www.youtube.com/watch?v=1").formats[0]
    assert (tier.label, tier.codec) == ("1440p60", "vp9")
    assert _selected(tier.format_spec, formats) == ["308", "140"]  # 标的是 VP9，就不能下成 AV1
