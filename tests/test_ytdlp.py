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
