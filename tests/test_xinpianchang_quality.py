"""新片场不能只看 progressive，高清 HLS / DASH 必须参与选择并严格下载。"""

import json

import httpx
import pytest

from qiuyuantu.convert import fetch, jobs, tasks
from qiuyuantu.convert import xinpianchang as downloader
from qiuyuantu.parser import base, manifests
from qiuyuantu.parser.base import FormatInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.quality import best_video_format
from qiuyuantu.parser.xinpianchang import XinPianChang

PAGE = "https://www.xinpianchang.com/a123"
HLS_URL = "https://cdn.test/master.m3u8"
MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720,FRAME-RATE=30,CODECS="avc1.640028,mp4a.40.2"
720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=3840x2160,FRAME-RATE=60,CODECS="avc1.640033,mp4a.40.2"
2160.m3u8
"""
MPD = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT10S">
<Period><AdaptationSet mimeType="video/mp4" codecs="avc1.640033">
<Representation id="v" width="3840" height="2160" frameRate="60" bandwidth="9000000">
<BaseURL>video.mp4</BaseURL></Representation></AdaptationSet>
<AdaptationSet mimeType="audio/mp4" codecs="mp4a.40.2">
<Representation id="a" bandwidth="128000"><BaseURL>audio.m4a</BaseURL></Representation>
</AdaptationSet></Period></MPD>"""


@pytest.fixture
def resources(monkeypatch):
    detail = {
        "video": {"vid": "MID", "appKey": "APP"},
        "cover": "cover.jpg",
        "title": "film",
        "author": {"userinfo": {"id": 1, "username": "author", "avatar": "avatar.jpg"}},
    }
    media = {
        "progressive": [{"url": "https://cdn.test/1080.mp4", "width": 1920, "height": 1080}],
        "hls": {"url": HLS_URL},
    }
    calls = []

    def response(request):
        calls.append(request)
        if str(request.url) == PAGE:
            return httpx.Response(
                200,
                text='<script id="__NEXT_DATA__">'
                + json.dumps({"props": {"pageProps": {"detail": detail}}})
                + "</script>",
            )
        if request.url.host == "mod-api.xinpianchang.com":
            assert request.url.path.endswith("/MID") and request.url.params["appKey"] == "APP"
            return httpx.Response(200, json={"data": {"resource": media}})
        if str(request.url) == HLS_URL:
            return httpx.Response(200, text=MASTER)
        if str(request.url) == "https://cdn.test/master.mpd":
            return httpx.Response(200, text=MPD)
        raise AssertionError("unexpected network " + str(request.url))

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(response), **kw)
    )
    return media, calls


async def test_hls_higher_than_progressive_is_the_primary_download(resources):
    info = await XinPianChang().parse_share_url(PAGE)
    best = best_video_format(info)
    assert info.video_url == "https://cdn.test/1080.mp4"
    assert (best.width, best.height, best.fps, best.label) == (3840, 2160, 60, "2160p60")
    assert best.format_spec.startswith("xpc:hls:") and not best.url
    assert "[height=2160]" in manifests.selector(best) and "[fps>=59.9]" in manifests.selector(best)
    assert not manifests.selector(best).endswith("/best")


async def test_dash_only_source_is_usable_and_audio_is_not_a_video_quality(resources):
    media, _ = resources
    media.clear()
    media["dash"] = {"url": "https://cdn.test/master.mpd"}
    info = await XinPianChang().parse_share_url(PAGE)
    assert not info.video_url and len(info.formats) == 1
    best = best_video_format(info)
    assert best.height == 2160 and best.format_spec.startswith("xpc:dash:")


@pytest.mark.parametrize(
    ("kind", "text", "url"), [("hls", MASTER, HLS_URL), ("dash", MPD, "https://cdn.test/master.mpd")]
)
def test_generated_selector_works_with_ytdlp_and_does_not_fall_back_to_lower_quality(kind, text, url):
    formats = manifests.tracks(text, url, kind)
    expected = manifests.download_format(max(formats, key=lambda fmt: fmt.get("height", 0)), f"xpc:{kind}")
    selector = fetch._sized_format(manifests.selector(expected), 1 << 30, False)
    context = {"formats": formats, "has_merged_format": kind == "hls", "incomplete_formats": False}
    [selected] = selector(context)
    assert selected["height"] == 2160 and selected["fps"] == 60
    context["formats"] = [fmt for fmt in formats if fmt.get("height", 0) != 2160]
    assert list(selector(context)) == []


async def test_same_dimensions_and_bitrate_do_not_collapse_different_video_codecs(resources, monkeypatch):
    async def text(self, url, **_kwargs):
        return MASTER.replace("2160.m3u8", "avc.m3u8") + (
            '#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=3840x2160,FRAME-RATE=60,CODECS="hvc1.1.6.L153,mp4a.40.2"\n'
            "hevc.m3u8\n"
        )

    monkeypatch.setattr(XinPianChang, "get_text", text)
    media, _ = resources
    formats = await XinPianChang().stream_formats(media, PAGE)
    high = [fmt for fmt in formats if fmt.height == 2160]
    assert len(high) == 2 and len({fmt.format_spec for fmt in high}) == 2
    assert all(f"[vcodec={fmt.codec}]" in manifests.selector(fmt) for fmt in high)


@pytest.mark.parametrize("text", ["not a manifest", MASTER * 30000])
def test_bad_or_unbounded_hls_does_not_create_fake_qualities(text):
    with pytest.raises(ParseError):
        manifests.tracks(text, HLS_URL, "hls")


@pytest.mark.parametrize(
    "text",
    [
        '<!DOCTYPE MPD [<!ENTITY secret SYSTEM "file:///etc/passwd">]><MPD/>',
        MPD.replace('type="static"', 'type="dynamic"'),
    ],
)
def test_dash_never_resolves_external_entities_or_accepts_live_manifests(text):
    with pytest.raises(ParseError):
        manifests.tracks(text, "https://cdn.test/master.mpd", "dash")


async def test_download_uses_fresh_manifest_and_checks_output_instead_of_reloading_page_in_ytdlp(
    resources, monkeypatch, tmp_path
):
    info = await XinPianChang().parse_share_url(PAGE)
    selected = best_video_format(info)
    _, calls = resources
    calls.clear()

    async def download(function, page, spec, directory, stem, **kwargs):
        assert page == HLS_URL and "[height=2160]" in spec
        assert not kwargs["fit"] and len(calls) == 3
        assert kwargs["http_headers"]["Referer"] == PAGE and kwargs["http_headers"]["Range"] == "bytes=0-"
        path = directory / (stem + ".mp4")
        path.write_bytes(b"verified output")
        return path

    monkeypatch.setattr(downloader, "run_ytdlp", download)
    monkeypatch.setattr(
        downloader.mp4_probe, "local_metadata", lambda _, **_kw: (FormatInfo(width=3840, height=2160, fps=60), 10)
    )
    path = await downloader.download(PAGE, selected.format_spec, tmp_path, "out", lambda *_: None)
    assert path.read_bytes() == b"verified output"


@pytest.mark.parametrize(
    "actual", [FormatInfo(width=1920, height=1080, fps=60), FormatInfo(width=3840, height=2160, fps=30)]
)
async def test_downgraded_resolution_or_framerate_is_rejected_and_removed(resources, monkeypatch, tmp_path, actual):
    selected = best_video_format(await XinPianChang().parse_share_url(PAGE))

    async def download(*_args, **_kwargs):
        path = tmp_path / "bad.mp4"
        path.write_bytes(b"partial")
        return path

    monkeypatch.setattr(downloader, "run_ytdlp", download)
    monkeypatch.setattr(downloader.mp4_probe, "local_metadata", lambda _, **_kw: (actual, 10))
    with pytest.raises(ValueError, match="来源返回的文件"):
        await downloader.download(PAGE, selected.format_spec, tmp_path, "out", lambda *_: None)
    assert list(tmp_path.iterdir()) == []


async def test_hls_output_uses_peak_framerate_for_variable_rate_content(resources, monkeypatch, tmp_path):
    selected = best_video_format(await XinPianChang().parse_share_url(PAGE))

    async def download(*_args, **_kwargs):
        path = tmp_path / "variable.mp4"
        path.write_bytes(b"variable frame rate")
        return path

    def metadata(_path, *, peak_fps):
        return FormatInfo(width=3840, height=2160, fps=60 if peak_fps else 30), 10

    monkeypatch.setattr(downloader, "run_ytdlp", download)
    monkeypatch.setattr(downloader.mp4_probe, "local_metadata", metadata)
    assert (await downloader.download(PAGE, selected.format_spec, tmp_path, "out", lambda *_: None)).exists()


async def test_missing_selected_quality_is_not_silently_replaced(resources, tmp_path):
    with pytest.raises(ValueError, match="所选清晰度"):
        await downloader.download(PAGE, "xpc:hls:3840x2160:120.0:8000000", tmp_path, "out", lambda *_: None)


async def test_task_dispatches_native_xpc_format(monkeypatch, tmp_path):
    async def download(page, spec, directory, stem, report, *, fit, cancelled):
        assert page == PAGE and spec == "xpc:hls:4k" and not fit
        return directory / "out.mp4"

    monkeypatch.setattr(downloader, "download", download)
    job = jobs.Job(id="xpc-test", type="download")
    assert await tasks._ytdlp(job, PAGE, "xpc:hls:4k", tmp_path, "out") == tmp_path / "out.mp4"


def test_manifest_download_retains_required_headers_and_existing_defaults(monkeypatch, tmp_path):
    seen = []

    def download(opts, url, directory, stem):
        seen.append(opts)
        path = directory / (stem + ".mp4")
        path.write_bytes(b"file")
        return {"requested_downloads": [{"filepath": str(path)}]}

    monkeypatch.setattr(fetch, "_ytdlp_run", download)
    for headers in (None, {"Referer": PAGE, "Range": "bytes=0-", "Cookie": "not-for-cdn"}):
        fetch.ytdlp_download(
            HLS_URL, "b", tmp_path, "out", report=lambda *_: None, cancelled=lambda: False, http_headers=headers
        )
    assert "http_headers" not in seen[0]
    assert seen[1]["http_headers"]["Referer"] == PAGE and seen[1]["http_headers"]["Range"] == "bytes=0-"
    assert "Cookie" not in seen[1]["http_headers"]
