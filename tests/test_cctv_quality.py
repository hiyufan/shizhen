"""央视档位以真实 MP4 为准，完整节目必须包含全部分段。"""

import asyncio
from pathlib import Path

import pytest

from qiuyuantu.convert import cctv, config, jobs, tasks
from qiuyuantu.convert.fetch import TooLarge
from qiuyuantu.parser import cctv as parser
from qiuyuantu.parser.base import FormatInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.quality import best_video_format

PAGE = "https://tv.cctv.com/2026/video.shtml"


def _group(key):
    return [{"url": f"http://vod.cntv.cdn20.com/{key}-{i}.mp4", "duration": "2.00"} for i in range(2)]


@pytest.fixture
def source(monkeypatch):
    data = {
        "status": "001",
        "hls_url": "https://vod.cntv.cdn20.com/low.m3u8",
        "video": {"totalLength": "4.00", "chapters3": _group("high"), "chapters4": _group("low")},
    }

    async def video_data(self, page):
        assert page == PAGE
        return data

    async def probe(url):
        # key 的编号与实际画质不一定同序。
        height = 720 if "high" in url else 270
        return FormatInfo(url=url, label=f"{height}p", width=height * 16 // 9, height=height, codec="H.264")

    monkeypatch.setattr(parser.CCTV, "video_data", video_data)
    monkeypatch.setattr(parser.mp4_probe, "probe", probe)
    return data


async def test_parser_keeps_compatible_hls_and_selects_actual_highest_mp4(source):
    info = await parser.CCTV().parse_share_url(PAGE)
    best = best_video_format(info)
    assert info.video_url.endswith("low.m3u8") and info.video_ext == "m3u8"
    assert best.height == 720 and best.format_spec == "cctv:chapters3:1280x720" and not best.url
    assert info.duration == 4 and info.page_url == PAGE


@pytest.mark.parametrize("bad", [[], [None], [{"url": "file:///etc/passwd", "duration": 4}], _group("x")[:1]])
def test_rejects_missing_tail_invalid_and_non_http_parts(bad):
    with pytest.raises(ParseError):
        parser.chapters({"video": {"totalLength": "4", "chapters": bad}}, "chapters")


@pytest.fixture
def downloads(source, monkeypatch):
    seen = []

    async def fetch(url, path, *, limit):
        assert url.startswith("https://vod.cntv.cdn20.com/")
        seen.append((url, limit))
        if limit < 3:
            raise TooLarge("文件太大")
        path.write_bytes(b"mp4")

    async def merge(args):
        assert args[args.index("-c") + 1] == "copy"
        manifest = Path(args[args.index("-i") + 1])
        assert manifest.read_text().splitlines() == ["ffconcat version 1.0", "file '0000.mp4'", "file '0001.mp4'"]
        assert {p.name for p in manifest.parent.glob("*.mp4")} == {"0000.mp4", "0001.mp4"}
        assert "http" not in " ".join(args)
        Path(args[-1]).write_bytes(b"merged")

    def metadata(path):
        seconds = 4 if path.name == "result.mp4" else 2
        return FormatInfo(width=1280, height=720, codec="H.264"), seconds

    monkeypatch.setattr(cctv, "fetch_bytes", fetch)
    monkeypatch.setattr(cctv.ffmpeg, "run", merge)
    monkeypatch.setattr(cctv.mp4_probe, "local_metadata", metadata)
    return seen


async def test_download_merges_all_parts_without_reencoding_and_cleans_temporary_files(downloads, tmp_path):
    out = await cctv.download(PAGE, "cctv:chapters3:1280x720", tmp_path, "result", lambda *_: None)
    assert out.read_bytes() == b"merged" and len(downloads) == 2
    assert downloads[1][1] == config.MAX_SOURCE_BYTES - 3
    assert list(tmp_path.iterdir()) == [out]


async def test_explicit_missing_quality_is_not_silently_downgraded(source, tmp_path):
    with pytest.raises(ValueError, match="所选清晰度"):
        await cctv.download(PAGE, "cctv:chapters3:1920x1080", tmp_path, "result", lambda *_: None)
    assert list(tmp_path.iterdir()) == []


async def test_aggregate_limit_applies_to_all_parts(downloads, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MAX_SOURCE_BYTES", 5)
    with pytest.raises(TooLarge):
        await cctv.download(PAGE, "cctv:chapters3:1280x720", tmp_path, "result", lambda *_: None)
    assert downloads[1][1] == 2 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stage", ["segment", "merged"])
async def test_incomplete_duration_is_never_returned_as_complete(downloads, monkeypatch, tmp_path, stage):
    def metadata(path):
        seconds = 4 if path.name == "result.mp4" else 2
        if (path.name == "result.mp4") == (stage == "merged"):
            seconds = 0.1
        return FormatInfo(width=1280, height=720, codec="H.264"), seconds

    monkeypatch.setattr(cctv.mp4_probe, "local_metadata", metadata)
    with pytest.raises(ValueError, match="完整"):
        await cctv.download(PAGE, "cctv:chapters3:1280x720", tmp_path, "result", lambda *_: None)
    assert list(tmp_path.iterdir()) == []


async def test_cancellation_removes_partial_segments(source, monkeypatch, tmp_path):
    async def fetch(url, path, **_kwargs):
        path.write_bytes(b"partial")
        raise asyncio.CancelledError

    monkeypatch.setattr(cctv, "fetch_bytes", fetch)
    with pytest.raises(asyncio.CancelledError):
        await cctv.download(PAGE, "cctv:chapters3:1280x720", tmp_path, "result", lambda *_: None)
    assert list(tmp_path.iterdir()) == []


async def test_conversion_can_choose_a_fitting_tier_without_changing_user_quality(source, monkeypatch):
    async def formats(_data):
        return [
            FormatInfo(height=2160, filesize=1),
            FormatInfo(height=1080, filesize=config.MAX_SOURCE_BYTES + 1),
            FormatInfo(height=720, filesize=100),
        ]

    monkeypatch.setattr(cctv, "chapter_formats", formats)
    assert (await cctv._selection(source, "default", fit=True)).height == 720


@pytest.mark.parametrize("fit", [False, True])
async def test_hls_fallback_passes_only_playable_hls_url(source, monkeypatch, tmp_path, fit):
    async def run(function, url, spec, *_args, **kwargs):
        assert url == source["hls_url"]
        assert spec == (
            f"bv*[height<={config.SOURCE_MAX_HEIGHT}]+ba/b[height<={config.SOURCE_MAX_HEIGHT}]" if fit else "bv*+ba/b"
        )
        assert kwargs["fit"] is fit
        return tmp_path / "result.mp4"

    if fit:
        source["video"] = {}
    monkeypatch.setattr(cctv, "run_ytdlp", run)
    spec = "default" if fit else "cctv:hls"
    assert await cctv.download(PAGE, spec, tmp_path, "result", lambda *_: None, fit=fit) == tmp_path / "result.mp4"


async def test_task_routes_cctv_quality_to_segment_downloader(monkeypatch, tmp_path):
    async def download(page, spec, directory, stem, report, *, fit, cancelled):
        assert page == PAGE and spec.startswith("cctv:") and not fit
        return directory / f"{stem}.mp4"

    monkeypatch.setattr(cctv, "download", download)
    job = jobs.Job(id="cctv-test", type="download")
    assert await tasks._ytdlp(job, PAGE, "cctv:chapters3:1280x720", tmp_path, "x") == tmp_path / "x.mp4"
