"""真实阶段通知、共享任务订阅，以及状态流断开后的资源回收。"""

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

from qiuyuantu import progress, web
from qiuyuantu.convert import config, youtube_native
from qiuyuantu.parser.base import VideoInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.web import limits
from qiuyuantu.web import parse as api

PAGE = "https://www.youtube.com/watch?v=iN1N8o3Miv4"
INFO = VideoInfo(video_url="https://cdn.example/video.mp4", cover_url="", title="示例", source="youtube")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    api.cache.clear()
    monkeypatch.setattr(api.stats, "record", lambda *_a, **_kw: None)
    for limiter in (limits.parse_limit, limits.parse_upstream_global):
        monkeypatch.setattr(limiter, "_buckets", {})

    async def safe(_url):
        return True

    monkeypatch.setattr(api, "is_safe_url_async", safe)


@pytest.mark.asyncio
async def test_stages_are_thread_safe_monotonic_and_fallback_is_sticky():
    state = progress.Progress()
    events = []
    state.subscribe(events.append)
    with progress.track(state):
        await asyncio.to_thread(progress.emit, "downloading", fallback=True)
        progress.emit("browser")
        progress.emit("not-a-public-stage")
        progress.emit("verifying")
        await asyncio.sleep(0)
    assert state.snapshot() == {"stage": "verifying", "fallback": True}
    assert [e["stage"] for e in events] == ["connecting", "downloading", "verifying"]
    state.closed = True
    state.update("cached")
    assert state.stage == "verifying"
    assert progress.current() is None


@pytest.mark.parametrize("failure", [False, True])
def test_stream_delivers_stages_and_preserves_json_result_contract(monkeypatch, failure):
    async def parse(_url):
        for stage in ("fallback", "browser", "downloading", "merging", "verifying"):
            progress.emit(stage, fallback=True)
            await asyncio.sleep(0)
        if failure:
            raise ParseError("blocked")
        return INFO

    monkeypatch.setattr(api, "parse_video_share_url", parse)
    response = TestClient(web.app).get("/api/parse/stream", params={"url": PAGE})
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["content-encoding"] == "identity"
    assert "no-transform" in response.headers["cache-control"]
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    stages = [event for event in events if event["type"] == "progress"]
    assert [s["stage"] for s in stages] == [
        "connecting",
        "extracting",
        "fallback",
        "browser",
        "downloading",
        "merging",
        "verifying",
    ]
    assert all(set(s) == {"type", "stage", "fallback"} for s in stages)
    assert all(s["fallback"] for s in stages[2:])
    result = events[-1]["result"]
    assert events[-1]["type"] == "result"
    assert result["code"] == (500 if failure else 200)
    if failure:
        assert result["reason"] == "blocked"
    else:
        assert result["data"]["sig"][INFO.video_url]
        assert TestClient(web.app).get("/api/parse", params={"url": PAGE}).json() == result
    assert not api._pending


@pytest.mark.asyncio
async def test_late_subscriber_sees_existing_stage_and_survives_other_disconnect(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def parse(_url):
        progress.emit("downloading", fallback=True)
        started.set()
        await release.wait()
        return INFO

    monkeypatch.setattr(api, "parse_video_share_url", parse)
    first, second = progress.Progress(), progress.Progress()
    with progress.track(first):
        a = asyncio.create_task(api._shared_parse(PAGE))
    await started.wait()
    await asyncio.sleep(0)
    with progress.track(second):
        b = asyncio.create_task(api._shared_parse(PAGE))
    await asyncio.sleep(0)
    flight = api._pending[PAGE]
    assert first.snapshot() == second.snapshot() == {"stage": "downloading", "fallback": True}
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    release.set()
    assert (await b)["code"] == 200
    assert not flight.progress.listeners and not api._pending


@pytest.mark.asyncio
async def test_stream_disconnect_stops_last_waiter(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def parse(_url):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(api, "parse_video_share_url", parse)
    stream = api._parse_events(PAGE, "127.0.0.1")
    assert '"connecting"' in await anext(stream)
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.wait_for(stream.aclose(), 1)
    assert stopped.is_set() and not api._pending


@pytest.mark.asyncio
async def test_native_helper_translates_only_public_stages(monkeypatch, tmp_path):
    script = tmp_path / "helper.py"
    script.write_text(
        "import json\n"
        "for s in ['download_start','merging','verify_decode_start','verified']:\n"
        " print(json.dumps({'stage':s,'cookie':'private-value','id':'iN1N8o3Miv4'}),flush=True)\n"
    )
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_SCRIPT", script)
    monkeypatch.setattr(config, "YOUTUBE_NATIVE_PYTHON", sys.executable)
    state, events = progress.Progress(), []
    state.subscribe(events.append)
    with progress.track(state):
        await youtube_native._worker("iN1N8o3Miv4", 720, tmp_path, "")
    assert [e["stage"] for e in events] == ["connecting", "downloading", "merging", "verifying"]
    assert "private-value" not in json.dumps(events)


def _deferred_parse(extra, gate=None):
    from qiuyuantu.parser import deferred
    from qiuyuantu.parser.base import FormatInfo

    async def enrich():
        if gate is not None:
            await gate.wait()
        if isinstance(extra, Exception):
            raise extra
        return [FormatInfo(label="1080p", url=extra, height=1080, width=1920)]

    async def parse(_url):
        info = VideoInfo(video_url=INFO.video_url, cover_url="", title="示例", source="douyin")
        await deferred.later(enrich(), lambda found: info.formats.extend(found))
        return info

    return parse


def _events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_shows_base_result_first_then_pushes_found_formats(monkeypatch):
    recorded = []
    monkeypatch.setattr(api.stats, "record", lambda *args, **kwargs: recorded.append(kwargs))
    monkeypatch.setattr(api, "parse_video_share_url", _deferred_parse("https://cdn.example/1080.mp4"))
    events = _events(TestClient(web.app).get("/api/parse/stream", params={"url": PAGE}))
    first, update = [event for event in events if event["type"] != "progress"]
    assert first["type"] == "result" and first["pending"] and first["result"]["data"]["formats"] == []
    assert update["type"] == "update"
    assert [f["url"] for f in update["result"]["data"]["formats"]] == ["https://cdn.example/1080.mp4"]
    assert update["result"]["data"]["sig"]["https://cdn.example/1080.mp4"]
    assert len(recorded) == 1 and recorded[0]["ok"]
    # 完整结果才进缓存，之后的 JSON 请求直接拿到补充后的清晰度。
    cached = TestClient(web.app).get("/api/parse", params={"url": PAGE}).json()
    assert cached["data"]["formats"] == update["result"]["data"]["formats"]
    assert not api._pending


def test_failed_enrichment_keeps_shown_result_and_ends_stream(monkeypatch):
    monkeypatch.setattr(api, "parse_video_share_url", _deferred_parse(RuntimeError("probe failed")))
    events = [
        e
        for e in _events(TestClient(web.app).get("/api/parse/stream", params={"url": PAGE}))
        if e["type"] != "progress"
    ]
    assert [e["type"] for e in events] == ["result", "done"]
    assert events[0]["result"]["code"] == 200


def test_json_parse_waits_for_enrichment(monkeypatch):
    monkeypatch.setattr(api, "parse_video_share_url", _deferred_parse("https://cdn.example/1080.mp4"))
    body = TestClient(web.app).get("/api/parse", params={"url": PAGE}).json()
    assert [f["url"] for f in body["data"]["formats"]] == ["https://cdn.example/1080.mp4"]


@pytest.mark.asyncio
async def test_enrichment_timeout_cancels_probe_and_keeps_base_result(monkeypatch):
    gate = asyncio.Event()
    monkeypatch.setattr(api, "DEFERRED_TIMEOUT", 0.01)
    monkeypatch.setattr(api, "parse_video_share_url", _deferred_parse("https://cdn.example/1080.mp4", gate))
    shown = []
    result = await api._parse(PAGE, shown.append)
    assert shown[0]["data"]["formats"] == result["data"]["formats"] == []


@pytest.mark.asyncio
async def test_callers_without_collector_wait_and_merge_in_place():
    info = await _deferred_parse("https://cdn.example/1080.mp4")(PAGE)
    assert [f.url for f in info.formats] == ["https://cdn.example/1080.mp4"]
