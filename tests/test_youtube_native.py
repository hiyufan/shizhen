"""原生兜底的缓存、签名文件代理、协议边界，以及超时后的子进程回收。"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from parse_video_py import status, web
from parse_video_py.convert import config, net, store, tasks
from parse_video_py.convert import youtube_native as native
from parse_video_py.parser.base import VideoInfo
from parse_video_py.parser.errors import ParseError
from parse_video_py.parser.ytdlp import YtDlp
from parse_video_py.web import limits
from parse_video_py.web import media as media_api
from parse_video_py.web import parse as parse_api
from parse_video_py.web import proxy as proxy_api

VIDEO_ID = "iN1N8o3Miv4"
PAGE = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_sources", {})
    monkeypatch.setattr(native, "_results", {})
    native._semaphore.cache_clear()
    monkeypatch.setattr(config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(native, "enabled", lambda: True)


class _Calls(list):
    """(视频, 清晰度, 出口) 的列表；.budgets 是每次传给下载器的大小 / 时限参数。"""

    budgets: list


@pytest.fixture
def worker(monkeypatch):
    calls = _Calls()
    calls.budgets = budgets = []

    async def fake(vid, height, work, proxy, **budget):
        calls.append((vid, height, proxy))
        budgets.append(budget)
        path = work / "native" / "video.mp4"
        path.parent.mkdir()
        path.write_bytes(b"verified-media" * 200)
        return {
            "id": vid,
            "file": str(path),
            "title": "韩语课程",
            "author": "作者",
            "duration": 592.1,
            "thumbnail": "https://i.ytimg.com/vi/example.jpg",
            "heights": [360, 720, 1080],
            "streams": [
                {"codec_type": "video", "width": 1280, "height": height, "avg_frame_rate": "30/1"},
                {"codec_type": "audio"},
            ],
        }

    monkeypatch.setattr(native, "_worker", fake)
    return calls


@pytest.mark.parametrize("url", [PAGE, f"https://youtu.be/{VIDEO_ID}?t=30", f"https://m.youtube.com/shorts/{VIDEO_ID}"])
def test_video_id_is_canonical(url):
    assert native.video_id(url) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
        f"https://example.com/?v={VIDEO_ID}",
        "file:///watch?v=iN1N8o3Miv4",
        "https://www.youtube.com/watch?v=../../etc/passwd",
    ],
)
def test_rejects_unrelated_hosts_and_ids(url):
    assert native.video_id(url) is None


@pytest.mark.asyncio
async def test_cache_and_public_metadata(worker, monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")
    info = await native.parse(PAGE)
    again = await native.parse(PAGE)
    assert worker == [(VIDEO_ID, 720, "http://warp:1080")]
    assert info == again
    assert info.source == "youtube" and info.height == 720 and info.duration == 592.1
    assert info.author.name == "作者" and info.title == "韩语课程"
    assert [f.format_spec for f in info.formats] == ["youtube-native:1080", "youtube-native:audio"]
    src = native.source_for_asset(info.video_url)
    assert src.fps == 30 and Path(src.path).is_file()
    assert not list(config.SOURCES_DIR.glob(".youtube-native-*"))
    assert native.source_for_asset(info.video_url + "?file=/etc/passwd") is None


@pytest.mark.asyncio
async def test_cached_alias_bypasses_extractors_and_busy_browser(worker, monkeypatch):
    info = await native.parse(PAGE)

    def no_extract(*_args):
        raise AssertionError("已有验证缓存不应再调用 yt-dlp")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(no_extract))
    async with native._semaphore():
        alias = await asyncio.wait_for(YtDlp().parse_share_url(f"https://youtu.be/{VIDEO_ID}?t=30"), 0.2)
    assert alias == info and len(worker) == 1


@pytest.mark.asyncio
async def test_parse_cache_drops_evicted_local_media(worker):
    info = await native.parse(PAGE)
    cache = parse_api.ParseCache()
    cache.put(PAGE, {"code": 200, "data": {"video_url": info.video_url}})
    assert cache.get(PAGE) is not None
    Path(native.source_for_asset(info.video_url).path).unlink()
    assert cache.get(PAGE) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["native", "fallback", "restricted", "cancel"])
async def test_recovery_overlaps_attempts_and_stops_unused_work(monkeypatch, winner):
    native_started, fallback_started = asyncio.Event(), asyncio.Event()
    stopped = []
    info = VideoInfo(video_url="https://example.com/video.mp4", cover_url="")

    async def fallback(_self, _url):
        fallback_started.set()
        try:
            await native_started.wait()
            if winner == "fallback":
                return info
            if winner == "restricted":
                raise RuntimeError("Sign in to confirm your age")
            await asyncio.Event().wait()
        finally:
            stopped.append("fallback")

    async def parse(_url):
        native_started.set()
        try:
            await fallback_started.wait()
            if winner == "native":
                return info
            await asyncio.Event().wait()
        finally:
            stopped.append("native")

    monkeypatch.setattr(YtDlp, "_fallback_info", fallback)
    monkeypatch.setattr(native, "parse", parse)
    task = asyncio.create_task(YtDlp()._race_fallbacks(PAGE))
    await asyncio.wait_for(asyncio.gather(native_started.wait(), fallback_started.wait()), 1)
    if winner == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif winner == "restricted":
        with pytest.raises(RuntimeError, match="confirm your age"):
            await task
    else:
        assert await asyncio.wait_for(task, 1) is info
    assert sorted(stopped) == ["fallback", "native"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["other_id", "escaped_file", "missing_audio", "empty_file"])
async def test_rejects_unverified_worker_result(worker, monkeypatch, tmp_path, bad):
    original = native._worker

    async def corrupt(*args, **kwargs):
        result = await original(*args, **kwargs)
        if bad == "other_id":
            result["id"] = "dQw4w9WgXcQ"
        elif bad == "escaped_file":
            outside = tmp_path / "outside.mp4"
            outside.write_bytes(b"outside" * 500)
            result["file"] = str(outside)
        elif bad == "missing_audio":
            result["streams"] = result["streams"][:1]
        else:
            Path(result["file"]).write_bytes(b"")
        return result

    monkeypatch.setattr(native, "_worker", corrupt)
    with pytest.raises(RuntimeError, match="经过验证"):
        await native.ensure(PAGE)
    assert not native._results and not store._sources


@pytest.mark.asyncio
async def test_parser_uses_native_after_both_extractors_fail(worker, monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")
    calls = []
    loop, seen = asyncio.get_running_loop(), asyncio.Event()
    parse = native.parse

    def blocked(_url, proxy=None):
        calls.append(proxy)
        if proxy:
            loop.call_soon_threadsafe(seen.set)
        raise RuntimeError("Sign in to confirm you’re not a bot")

    async def after_fallback(url):
        await asyncio.wait_for(seen.wait(), 1)
        return await parse(url)

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(blocked))
    monkeypatch.setattr(native, "parse", after_fallback)
    info = await YtDlp().parse_share_url(PAGE)
    assert calls == [None, "http://warp:1080"]
    assert native.source_for_asset(info.video_url) is not None


@pytest.mark.asyncio
async def test_age_or_deleted_content_does_not_start_native(worker, monkeypatch):
    monkeypatch.delenv("PARSE_VIDEO_FALLBACK_PROXY", raising=False)

    def restricted(*_a):
        raise RuntimeError("Sign in to confirm your age")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(restricted))
    with pytest.raises(RuntimeError, match="confirm your age"):
        await YtDlp().parse_share_url(PAGE)
    assert not worker


@pytest.mark.asyncio
async def test_metadata_success_with_forbidden_media_uses_native(worker, monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")
    monkeypatch.setattr(net, "_FALLBACK_URLS", {})
    media = "https://rr1.googlevideo.com/videoplayback"
    monkeypatch.setattr(
        YtDlp, "_extract", staticmethod(lambda *_a: {"url": media, "ext": "mp4", "extractor_key": "Youtube"})
    )
    checked = []
    seen = asyncio.Event()
    parse = native.parse

    async def forbidden(info):
        checked.append(net.proxy_for_url(info.video_url))
        if len(checked) == 2:
            seen.set()
        response = httpx.Response(403, request=httpx.Request("GET", media))
        response.raise_for_status()

    async def after_fallback(url):
        await asyncio.wait_for(seen.wait(), 1)
        return await parse(url)

    monkeypatch.setattr(native, "check_media", forbidden)
    monkeypatch.setattr(native, "parse", after_fallback)
    info = await YtDlp().parse_share_url(PAGE)
    assert len(checked) == 2 and checked[-1] == "http://warp:1080"
    assert native.is_asset_url(info.video_url) and len(worker) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type,body,ok", [("video/mp4", b"video", True), ("text/html", b"error", False), ("video/mp4", b"", False)]
)
async def test_media_probe_closes_response_and_rejects_empty_or_text(monkeypatch, content_type, body, ok):
    seen = []

    def transport(request):
        seen.append(request.headers["range"])
        return httpx.Response(206, headers={"content-type": content_type}, stream=httpx.ByteStream(body))

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(net, "safe_client", lambda **_kw: client)
    info = VideoInfo(video_url="https://rr1.googlevideo.com/a", cover_url="")
    if ok:
        await native.check_media(info)
    else:
        with pytest.raises(ParseError):
            await native.check_media(info)
    assert seen == ["bytes=0-1023"] and client.is_closed


@pytest.mark.asyncio
async def test_native_failure_preserves_original_blocked_error(worker, monkeypatch):
    def blocked(*_args):
        raise RuntimeError("Sign in to confirm you’re not a bot")

    async def unavailable(*_args):
        raise RuntimeError("Video unavailable")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(blocked))
    monkeypatch.setattr(native, "parse", unavailable)
    with pytest.raises(RuntimeError, match="not a bot"):
        await YtDlp().parse_share_url(PAGE)


@pytest.mark.asyncio
async def test_source_fetch_reuses_native_file_without_network(worker, monkeypatch):
    info = await native.parse(PAGE)
    src = native.source_for_asset(info.video_url)
    path = await tasks._download_source_file(None, "another-source", info.video_url, {}, PAGE, "")
    assert path.read_bytes() == Path(src.path).read_bytes()
    Path(src.path).unlink()
    with pytest.raises(ValueError, match="过期"):
        await tasks._download_source_file(None, "another-source", info.video_url, {}, PAGE, "")


def test_cached_proxy_range_and_prepare(worker, monkeypatch):
    info = asyncio.run(native.parse(PAGE))
    src = native.source_for_asset(info.video_url)
    client = TestClient(web.app)
    for limiter in (limits.proxy_limit, limits.job_limit):
        monkeypatch.setattr(limiter, "_buckets", {})
    sent = []
    monkeypatch.setattr(limits.proxy_daily, "add", lambda ip, size: sent.append(size))

    async def no_dns(*_a):
        raise AssertionError("本地已签名媒体不应查询 DNS 或访问网络")

    monkeypatch.setattr(proxy_api, "is_safe_url_async", no_dns)
    monkeypatch.setattr(media_api, "is_safe_url_async", no_dns)
    url = info.video_url
    sig = net.sign(url)
    result = client.get("/api/proxy", params={"url": url, "sig": sig}, headers={"Range": "bytes=10-19"})
    assert result.status_code == 206
    assert result.content == Path(src.path).read_bytes()[10:20]
    assert result.headers["content-range"] == f"bytes 10-19/{Path(src.path).stat().st_size}"
    assert sum(sent) == 10
    assert client.get("/api/proxy", params={"url": url, "sig": "wrong"}).status_code == 403
    prepared = client.post("/api/prepare", json={"url": url, "sig": sig}).json()
    assert prepared["ready"] and prepared["source"]["id"] == src.id
    Path(src.path).unlink()
    assert client.get("/api/proxy", params={"url": url, "sig": sig}).status_code == 410


@pytest.mark.asyncio
async def test_status_checks_local_cached_bytes(worker):
    info = await native.parse(PAGE)
    await status._probe_youtube_media(info)
    Path(native.source_for_asset(info.video_url).path).write_bytes(b"")
    with pytest.raises(ParseError, match="没有数据"):
        await status._probe_youtube_media(info)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_or_cancellation_reaps_worker_child_process(monkeypatch, tmp_path, cancel):
    script = tmp_path / "worker.py"
    pidfile = tmp_path / "child.pid"
    script.write_text(
        "import subprocess,sys,json,time\n"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)\n"
        f"open({str(pidfile)!r},'w').write(str(p.pid))\n"
        "print(json.dumps({'stage':'_process','group':p.pid}),flush=True)\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_SCRIPT", script)
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_PYTHON", sys.executable)
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_TIMEOUT", 1.0)
    task = asyncio.create_task(native._worker(VIDEO_ID, 720, tmp_path, ""))
    if cancel:
        for _ in range(200):
            if pidfile.exists():
                break
            await asyncio.sleep(0.01)
        assert pidfile.exists()
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
        await task
    child = int(pidfile.read_text())
    state = Path(f"/proc/{child}/stat")
    assert not state.exists() or state.read_text().split()[2] == "Z"


# --------------------------------------------------------------------------- 解析 / 任务的预算


@pytest.mark.asyncio
async def test_parse_fits_its_time_budget_but_jobs_get_the_full_limit(worker, tmp_path):
    info = await native.parse(PAGE)
    assert worker.budgets[0] == {
        "fit": True,
        "max_bytes": config.YOUTUBE_NATIVE_PARSE_BYTES,
        "timeout": config.YOUTUBE_NATIVE_TIMEOUT,
        "on_event": None,
    }
    reports = []
    out = await native.download(PAGE, "youtube-native:1080", tmp_path / "out", "v", lambda *a: reports.append(a))
    assert worker[-1][1] == 1080 and out.is_file()
    budget = worker.budgets[-1]
    assert "fit" not in budget and budget["max_bytes"] == config.MAX_SOURCE_BYTES
    assert budget["timeout"] == config.YOUTUBE_NATIVE_JOB_TIMEOUT > config.YOUTUBE_NATIVE_TIMEOUT
    # 解析时下好的那份清晰度一样就直接用，不再开浏览器
    same = await native.download(PAGE, "youtube-native:720", tmp_path / "out", "same")
    assert len(worker) == 2 and same.read_bytes() == Path(native.source_for_asset(info.video_url).path).read_bytes()


@pytest.mark.asyncio
async def test_native_files_are_hardlinked_not_copied(worker, tmp_path):
    info = await native.parse(PAGE)
    src = Path(native.source_for_asset(info.video_url).path)
    out = await native.download(PAGE, "youtube-native:720", tmp_path / "out", "v")
    path = await tasks._download_source_file(None, "another", info.video_url, {}, PAGE, "")
    assert out.stat().st_ino == path.stat().st_ino == src.stat().st_ino
    src.unlink()  # 缓存过期删掉原文件，已经交给任务的那份还在
    assert out.read_bytes() and path.read_bytes()


@pytest.mark.asyncio
async def test_status_probe_skips_cache_and_takes_lowest_quality(worker, monkeypatch):
    await native.parse(PAGE)

    def blocked(_url, proxy=None):
        raise RuntimeError("Sign in to confirm you’re not a bot")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(blocked))
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")
    # 用户再解析：命中缓存
    await YtDlp().parse_share_url(PAGE)
    assert len(worker) == 1
    # 自检：每次都真去 YouTube 拿一份 144p，不被缓存续期骗成「正常」
    for _ in range(2):
        ok, reason, _ = await status.probe_one("youtube", [PAGE], YtDlp().parse_share_url)
        assert (ok, reason) == (True, "")
    assert [c[1] for c in worker] == [720, 144, 144]
    assert native.cached(PAGE)[0].height == 720  # 用户的缓存没被自检换掉


@pytest.mark.asyncio
async def test_busy_browser_slots_refuse_parse_quickly(worker, monkeypatch):
    monkeypatch.setattr(native, "_PARSE_SLOT_WAIT", 0.05)
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_CONCURRENCY", 1)
    native._semaphore.cache_clear()
    async with native._semaphore():
        with pytest.raises(native.Refused) as err:
            await native.parse(PAGE)
    assert err.value.reason == "timeout" and "正忙" in str(err.value)


@pytest.mark.asyncio
async def test_too_large_is_reported_instead_of_the_bot_check(worker, monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")

    def blocked(_url, proxy=None):
        raise RuntimeError("Sign in to confirm you’re not a bot")

    async def too_large(_url):
        raise native.Refused("too_large", "40 分钟的视频在 144p 也有约 150 MB")

    monkeypatch.setattr(YtDlp, "_extract", staticmethod(blocked))
    monkeypatch.setattr(native, "parse", too_large)
    with pytest.raises(ParseError) as err:
        await YtDlp().parse_share_url(PAGE)
    assert err.value.reason == "too_large" and "40 分钟" in str(err.value)


@pytest.mark.asyncio
async def test_worker_reports_size_plan_bytes_and_too_large(monkeypatch, tmp_path):
    script = tmp_path / "helper.py"
    script.write_text(
        "import json,sys\n"
        "assert '--fit' in sys.argv and sys.argv[sys.argv.index('--max-bytes')+1] == '1000'\n"
        "for e in [{'stage':'plan','bytes':400},{'stage':'bytes','done':200},{'stage':'merging'},"
        "{'stage':'too_large','height':144,'bytes':5e8,'duration':2400,'limit':1000},{'stage':'failed','error':'x'}]:\n"
        " print(json.dumps(e),flush=True)\n"
        "sys.exit(1)\n"
    )
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_SCRIPT", script)
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_PYTHON", sys.executable)
    reports = []
    with pytest.raises(native.Refused) as err:
        on_event = native._job_progress(lambda *a: reports.append(a))
        await native._worker(VIDEO_ID, 720, tmp_path, "", fit=True, max_bytes=1000, on_event=on_event)
    assert err.value.reason == "too_large" and "40 分钟的视频在 144p 约 500 MB" in str(err.value)
    assert [round(p, 2) for p, _ in reports] == [0.02, 0.45, 0.92]


def test_hardlinked_copies_count_once_toward_disk_quota(monkeypatch):
    monkeypatch.setattr(store, "_DATA_DIRS", (config.SOURCES_DIR, config.OUTPUTS_DIR))
    config.SOURCES_DIR.mkdir(parents=True)
    config.OUTPUTS_DIR.mkdir(parents=True)
    src = config.SOURCES_DIR / "a.mp4"
    src.write_bytes(b"x" * 1000)
    native.link_or_copy(src, config.OUTPUTS_DIR / "b.mp4")
    assert store.disk_usage() == 1000
