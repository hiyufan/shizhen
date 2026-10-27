"""HLS 严格分段范围、安全主机、有限时长和临时文件清理。"""

import asyncio
from pathlib import Path

import httpx
import pytest

from qiuyuantu.convert import artwork, ffmpeg, jobs, tasks
from qiuyuantu.convert.fetch import TooLarge
from qiuyuantu.parser.base import FormatInfo
from qiuyuantu.parser.errors import ParseError

BASE = "https://mvod.itunes.apple.com/art/playlist.m3u8"
MASTER = """#EXTM3U
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=100,URI="iframe.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=9000,CODECS="avc1.640028",RESOLUTION=2160x2160
large.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000,CODECS="hvc1.1.6",RESOLUTION=1080x1080
hevc.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000,CODECS="avc1.640028",RESOLUTION=1080x1080
best.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000,CODECS="avc1.640028",RESOLUTION=720x720
small.m3u8
"""
PLAYLIST = """#EXTM3U
#EXT-X-MAP:URI="movie.mp4",BYTERANGE="4@0"
#EXTINF:3,
#EXT-X-BYTERANGE:4@4
movie.mp4
#EXTINF:3,
#EXT-X-BYTERANGE:4
movie.mp4
#EXT-X-ENDLIST
"""


def test_hls_selects_highest_video_and_keeps_exact_ranges():
    assert artwork.choose_stream(MASTER, BASE) == BASE.replace("playlist", "large")
    assert artwork.media_parts(PLAYLIST, BASE) == [
        (BASE.replace("playlist.m3u8", "movie.mp4"), r) for r in [(0, 4), (4, 4), (8, 4)]
    ]


def test_hls_same_resolution_chooses_higher_bandwidth():
    master = MASTER.replace("2160x2160", "1080x1080")
    assert artwork.choose_stream(master, BASE) == BASE.replace("playlist", "large")


def test_hls_same_resolution_keeps_the_high_frame_rate_track():
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=9000,CODECS="avc1.640028",RESOLUTION=2160x2160,FRAME-RATE=30
large.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=8000,CODECS="hvc1.1.6",RESOLUTION=2160x2160,FRAME-RATE=60
smooth.m3u8
"""
    assert artwork.choose_stream(master, BASE) == BASE.replace("playlist", "smooth")


@pytest.mark.parametrize(
    "text",
    [
        PLAYLIST.replace("#EXT-X-ENDLIST", ""),
        PLAYLIST.replace("#EXTINF:3", "#EXTINF:121"),
        PLAYLIST.replace("#EXTINF:3", "#EXTINF:nan"),
        PLAYLIST.replace("#EXTM3U", '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"'),
        PLAYLIST.replace("#EXTINF:3,\n#EXT-X-BYTERANGE:4\nmovie.mp4", "#EXTINF:3,\n#EXT-X-BYTERANGE:4\nother.mp4"),
        PLAYLIST.replace("4@4", "0@4"),
        PLAYLIST.replace("4@4", "4000000000@4"),
    ],
)
def test_hls_rejects_live_encrypted_oversized_or_invalid_segments(text):
    with pytest.raises((ValueError, TooLarge)):
        artwork.media_parts(text, BASE)


@pytest.mark.parametrize("url", ["https://evil.test/video", "http://mvod.itunes.apple.com/video", "file:///etc/passwd"])
def test_hls_never_accepts_external_or_local_segment_urls(url):
    with pytest.raises(ParseError):
        artwork.media_parts(PLAYLIST.replace('URI="movie.mp4"', f'URI="{url}"'), BASE)


def test_hls_rejects_master_encryption_and_unusable_streams():
    with pytest.raises(ValueError, match="加密"):
        artwork.choose_stream("#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES\n" + MASTER, BASE)
    with pytest.raises(ValueError, match="H.264"):
        artwork.choose_stream(MASTER.replace("avc1", "vp09").replace("hvc1", "vp09"), BASE)


@pytest.mark.parametrize(
    ("status", "content_range", "body", "error"),
    [
        (200, "", b"whole file", "正确"),
        (206, "bytes 0-3/12", b"data", "正确"),
        (206, "bytes 4-7/12", b"ab", "完整"),
        (206, "bytes 4-7/12", b"toolong", "大小"),
    ],
)
async def test_hls_range_download_rejects_bad_server_responses(
    tmp_path, monkeypatch, status, content_range, body, error
):
    def client(*, for_url, **kwargs):
        def handle(request):
            assert request.headers["range"] == "bytes=4-7"
            assert request.headers["referer"] == "https://music.apple.com/"
            return httpx.Response(status, headers={"Content-Range": content_range}, content=body)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(artwork, "safe_client", client)
    with pytest.raises((ValueError, TooLarge), match=error):
        await artwork._piece(BASE, tmp_path / "piece", (4, 4), 12)


async def test_apple_segments_join_locally_without_passing_network_urls_to_ffmpeg(tmp_path, monkeypatch):
    async def metadata(_self, page):
        return {"videoArtwork": {"dictionary": {"motionDetailSquare": {"video": BASE}}}}, page

    async def text(_self, url):
        return MASTER if url == BASE else PLAYLIST

    async def piece(_url, path, byte_range, _limit):
        path.write_bytes(bytes(range(*[byte_range[0], sum(byte_range)])))

    async def encode(args):
        source = args[args.index("-i") + 1]
        assert not source.startswith("http")

        assert Path(source).read_bytes() == bytes(range(12))
        Path(args[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(artwork.AppleMusic, "artwork", metadata)
    monkeypatch.setattr(artwork.AppleMusic, "get_text", text)
    monkeypatch.setattr(artwork, "_piece", piece)
    monkeypatch.setattr(ffmpeg, "run", encode)
    monkeypatch.setattr(artwork, "local_metadata", lambda _path, **_kw: (FormatInfo(width=2160, height=2160), 6))
    job = jobs.Job(id="art", type="prepare")
    result = await tasks._ytdlp(
        job, "https://music.apple.com/us/album/100", "apple-artwork:motionDetailSquare", tmp_path, "out"
    )
    assert result.read_bytes() == b"mp4" and list(tmp_path.iterdir()) == [result]
    assert job.progress == pytest.approx(0.9)


async def test_cancelled_artwork_download_cleans_temporary_files(tmp_path, monkeypatch):
    async def source(_page, _spec, directory, _report):
        (directory / "partial.bin").write_bytes(b"partial")
        (tmp_path / "out.mp4").write_bytes(b"partial")
        raise asyncio.CancelledError

    monkeypatch.setattr(artwork, "_apple_source", source)
    with pytest.raises(asyncio.CancelledError):
        await artwork.download("https://music.apple.com/us/album/100", "", tmp_path, "out", lambda *_: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "measured",
    [None, (FormatInfo(width=1080, height=1080, fps=60), 6), (FormatInfo(width=2160, height=2160, fps=30), 6)],
)
async def test_actual_resolution_or_frame_rate_mismatch_cleans_output(tmp_path, monkeypatch, measured):
    async def source(_page, _spec, directory, _report):
        path = directory / "source.bin"
        path.write_bytes(b"source")
        return path, FormatInfo(width=2160, height=2160, fps=60)

    async def copy(args):
        assert args[args.index("-c") + 1] == "copy" and "-vf" not in args
        Path(args[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(artwork, "_apple_source", source)
    monkeypatch.setattr(ffmpeg, "run", copy)
    monkeypatch.setattr(artwork, "local_metadata", lambda _path, **_kw: measured)
    with pytest.raises(ValueError, match="动态封面"):
        await artwork.download("https://music.apple.com/us/album/100", "", tmp_path, "out", lambda *_: None)
    assert not list(tmp_path.iterdir())


async def test_variable_rate_output_is_validated_using_the_hls_peak(tmp_path, monkeypatch):
    def measured(path, *, peak_fps):
        assert path == tmp_path / "output.mp4" and peak_fps
        return FormatInfo(width=2160, height=2160, fps=60), 6

    monkeypatch.setattr(artwork, "local_metadata", measured)
    await artwork._validate_output(tmp_path / "output.mp4", FormatInfo(width=2160, height=2160, fps=60))
