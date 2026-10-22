"""真实阶段通知、共享任务订阅，以及状态流断开后的资源回收。"""

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

from parse_video_py import progress, web
from parse_video_py.convert import config, youtube_native
from parse_video_py.parser.base import VideoInfo
from parse_video_py.parser.errors import ParseError
from parse_video_py.web import limits
from parse_video_py.web import parse as api

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
