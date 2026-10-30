"""媒体探测只读有界首尾片段，按真实视频轨道标档，不下载完整文件。"""

import asyncio
import struct

import httpx
import pytest

from qiuyuantu.parser import mp4_probe


def _box(kind, content):
    return struct.pack(">I4s", len(content) + 8, kind) + content


def _movie(*, width=1080, height=1920, codec=b"avc1", handler=b"vide", version=0, timings=None):
    tkhd = bytes(76) + struct.pack(">II", width << 16, height << 16)
    # stsd 的 visual sample entry 尺寸独立于 tkhd 的显示尺寸。
    entry = struct.pack(">I4s", 80, codec) + bytes(24) + struct.pack(">HH", width, height) + bytes(44)
    stsd = _box(b"stsd", bytes(4) + struct.pack(">I", 1) + entry)
    stsz = _box(b"stsz", bytes(8) + struct.pack(">I", 300))
    mdhd = (
        bytes(12) + struct.pack(">II", 30000, 300000)
        if not version
        else b"\x01" + bytes(19) + struct.pack(">IQ", 30000, 300000)
    )
    mdia = _box(b"hdlr", bytes(8) + handler) + _box(b"mdhd", mdhd)
    stts = (
        _box(
            b"stts", bytes(4) + struct.pack(">I", len(timings)) + b"".join(struct.pack(">II", *row) for row in timings)
        )
        if timings
        else b""
    )
    mdia += _box(b"minf", _box(b"stbl", stsd + stsz + stts))
    return _box(b"moov", _box(b"trak", _box(b"tkhd", tkhd) + _box(b"mdia", mdia)))


@pytest.mark.parametrize("version", [0, 1])
def test_reads_real_coded_dimensions_duration_and_framerate(version):
    fmt, duration = mp4_probe.metadata(_box(b"ftyp", b"isom") + _movie(version=version))
    assert (fmt.width, fmt.height, fmt.fps, fmt.codec, duration) == (1080, 1920, 30, "H.264", 10)


@pytest.mark.parametrize("version", [0, 1])
def test_hls_peak_framerate_does_not_confuse_variable_rate_with_average(tmp_path, version):
    path = tmp_path / "variable.mp4"
    path.write_bytes(_movie(version=version, timings=[(150, 500), (150, 1500)]))
    average, duration = mp4_probe.local_metadata(path)
    peak, _ = mp4_probe.local_metadata(path, peak_fps=True)
    assert (average.fps, peak.fps, duration) == (30, 60, 10)


@pytest.mark.parametrize("data", [b"not a video", _movie(handler=b"soun"), _movie(codec=b"encv"), _movie()[:-1]])
def test_rejects_non_video_encrypted_or_incomplete_metadata(data):
    assert mp4_probe.metadata(data) is None


def test_finds_complete_moov_at_the_end_without_scanning_the_whole_file():
    fmt, _ = mp4_probe.metadata(b"moov" + b"partial mdat" * 100 + _movie(), tail=True)
    assert fmt.width == 1080 and fmt.height == 1920


def test_local_probe_skips_large_media_payload_and_reads_complete_track_table(tmp_path):
    path = tmp_path / "large.mp4"
    movie = _movie()
    path.write_bytes(_box(b"mdat", bytes(2 << 20)) + movie)
    fmt, duration = mp4_probe.local_metadata(path)
    assert (fmt.width, fmt.height, duration) == (1080, 1920, 10)
    # 不信任截断文件的 moov 长度。
    path.write_bytes(_box(b"mdat", b"1234") + movie[:-1])
    assert mp4_probe.local_metadata(path) is None


def test_local_probe_bounds_track_table_allocation(tmp_path):
    path = tmp_path / "huge-table.mp4"
    with path.open("wb") as target:
        target.write(struct.pack(">I4s", (16 << 20) + 1, b"moov"))
        target.truncate((16 << 20) + 1)
    assert mp4_probe.local_metadata(path) is None


@pytest.fixture
def network(monkeypatch):
    calls = []

    def install(handler):
        def transport(request):
            calls.append(request)
            assert request.headers["accept-encoding"] == "identity"
            return handler(request)

        monkeypatch.setattr(
            mp4_probe,
            "safe_client",
            lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(transport), **{k: v for k, v in kwargs.items() if k != "for_url"}
            ),
        )
        return calls

    return install


async def test_small_head_is_enough_and_labels_actual_not_requested_resolution(network):
    body = _box(b"ftyp", b"isom") + _movie(width=1080, height=1920)
    calls = network(lambda _: httpx.Response(200, headers={"Content-Length": str(len(body))}, content=body))
    fmt = await mp4_probe.probe("https://cdn.test/2160p-request.mp4")
    assert len(calls) == 1 and fmt.label == "1080p"
    assert fmt.filesize == len(body) and fmt.url.endswith("2160p-request.mp4")


async def test_reads_only_bounded_head_and_tail_when_movie_metadata_is_at_end(network):
    body = _box(b"ftyp", b"isom") + _box(b"mdat", bytes(2100000)) + _movie()

    def response(request):
        start, end = map(int, request.headers["range"].removeprefix("bytes=").split("-"))
        end = min(end, len(body) - 1)
        return httpx.Response(
            206, headers={"Content-Range": f"bytes {start}-{end}/{len(body)}"}, content=body[start : end + 1]
        )

    calls = network(response)
    assert (await mp4_probe.probe("https://cdn.test/end.mp4")).height == 1920
    assert len(calls) == 2
    assert calls[0].headers["range"] == "bytes=0-65535"
    assert calls[1].headers["range"].startswith(f"bytes={len(body) - 524288}-")


def _ranged(body):
    def response(request):
        start, end = map(int, request.headers["range"].removeprefix("bytes=").split("-"))
        end = min(end, len(body) - 1)
        return httpx.Response(
            206, headers={"Content-Range": f"bytes {start}-{end}/{len(body)}"}, content=body[start : end + 1]
        )

    return response


def _large_movie():
    # moov 内多一个 free 子 box，模拟长视频超过首段读取量的样本表。
    movie = _movie()
    return _box(b"moov", movie[8:] + _box(b"free", bytes(100_000)))


async def test_large_front_metadata_reads_exactly_the_rest_of_the_movie_box(network):
    head = _box(b"ftyp", b"isom") + _large_movie()
    body = head + _box(b"mdat", bytes(2_000_000))
    calls = network(_ranged(body))
    assert (await mp4_probe.probe("https://cdn.test/front.mp4")).height == 1920
    end = len(head)
    assert [c.headers["range"] for c in calls] == ["bytes=0-65535", f"bytes=65536-{end - 1}"]


async def test_continuation_falls_back_to_bounded_head_when_range_is_ignored(network):
    body = _box(b"ftyp", b"isom") + _large_movie() + _box(b"mdat", bytes(500_000))
    calls = network(lambda _: httpx.Response(200, headers={"Content-Length": str(len(body))}, content=body))
    assert (await mp4_probe.probe("https://cdn.test/no-range.mp4")).height == 1920
    assert len(calls) == 3


async def test_server_ignoring_range_cannot_make_probe_download_the_whole_file(network):
    read = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            first = _box(b"ftyp", b"isom") + _movie()
            for index in range(100):
                read.append(index)
                yield first.ljust(32768, b"\0") if not index else bytes(32768)

        async def aclose(self):
            read.append("closed")

    network(lambda _: httpx.Response(200, headers={"Content-Length": "3276800"}, stream=Stream()))
    assert (await mp4_probe.probe("https://cdn.test/ignored-range.mp4")).height == 1920
    assert read == [0, 1, "closed"]


@pytest.mark.parametrize("headers", [{"Content-Range": "bytes 1-20/1000"}, {"Content-Encoding": "gzip"}])
async def test_bad_ranges_and_compressed_responses_are_not_trusted(network, headers):
    network(lambda _: httpx.Response(206, headers=headers, content=b"not metadata"))
    assert await mp4_probe.probe("https://cdn.test/bad.mp4") is None


async def test_edge_fallback_is_streamed_and_does_not_expose_internal_edge_url(network, monkeypatch):
    body = _movie()

    def response(request):
        if request.url.host == "cdn.test":
            return httpx.Response(403)
        return httpx.Response(200, headers={"Content-Length": str(len(body))}, content=body)

    monkeypatch.setattr(mp4_probe.relay, "edge_media_enabled", lambda: True)
    monkeypatch.setattr(mp4_probe.relay, "edge_media_url", lambda *_: "https://edge.test/media?token=test")
    calls = network(response)
    info = await mp4_probe.probe("https://cdn.test/video.mp4")
    assert [request.url.host for request in calls] == ["cdn.test", "edge.test"]
    assert info.url == "https://cdn.test/video.mp4" and info.height == 1920


async def test_cancelled_probe_closes_the_response_without_waiting_for_more_data(network):
    reading, closed = asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            reading.set()
            await asyncio.Future()

        async def aclose(self):
            closed.set()

    network(lambda _: httpx.Response(200, stream=Stream()))
    task = asyncio.create_task(mp4_probe.probe("https://cdn.test/slow.mp4"))
    await asyncio.wait_for(reading.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
