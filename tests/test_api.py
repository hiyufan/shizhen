"""/api/parse 的成功 / 失败两条路，以及转发类接口的签名、SSRF 拦截。

平台解析器一律换成假的，不碰网络。7b42b30 那次全站 500 就是因为只测了失败路径，
所以成功路径在这里必须有。
"""

import asyncio
import hashlib
import hmac
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from parse_video_py import web
from parse_video_py.convert import limits, net, relay
from parse_video_py.parser.base import FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from parse_video_py.parser.errors import ParseError

SHARE = "https://v.douyin.com/abc123/"


def _info(**kw) -> VideoInfo:
    base = dict(
        video_url="https://v3-web.douyinvod.com/play/1.mp4",
        cover_url="https://p3-sign.douyinpic.com/cover.jpeg",
        title="标题",
        music_url="https://sf3-cdn.douyinstatic.com/music.mp3",
        images=[ImgInfo(url="https://p3-sign.douyinpic.com/1.webp",
                        live_photo_url="https://v3-web.douyinvod.com/live/1.mp4")],
        author=VideoAuthor(name="作者"),
        source="douyin",
        formats=[FormatInfo(label="1080p", url="https://v3-web.douyinvod.com/play/1080.mp4")],
    )
    base.update(kw)
    return VideoInfo(**base)


@pytest.fixture
def calls(monkeypatch):
    """记下解析器被调了几次、拿到的是什么链接；默认返回 _info()。"""
    state = {"urls": [], "result": _info()}

    async def fake_parse(url):
        state["urls"].append(url)
        if isinstance(state["result"], BaseException):
            raise state["result"]
        return state["result"]

    async def always_safe(url):
        return True

    monkeypatch.setattr(web, "parse_video_share_url", fake_parse)
    monkeypatch.setattr(web, "is_safe_url_async", always_safe)
    return state


@pytest.fixture
def recorded(monkeypatch):
    rows = []
    monkeypatch.setattr(web.stats, "record", lambda kind, ip, **kw: rows.append((kind, kw)))
    return rows


@pytest.fixture
def client(monkeypatch):
    web._parse_cache.clear()
    for rl in (limits.parse_limit, limits.proxy_limit, limits.job_limit):
        monkeypatch.setattr(rl, "_buckets", {})
    # 不进 with 块就不跑 lifespan：不起清理任务，也不预热抖音的 Chromium
    return TestClient(web.app)


# --------------------------------------------------------------------------- 成功路径


def test_success_returns_data_and_signs_every_url(client, calls, recorded):
    body = client.get("/api/parse", params={"url": SHARE}).json()

    assert body["code"] == 200, body
    data = body["data"]
    assert data["source"] == "douyin"
    assert data["share_url"] == SHARE
    assert data["author"]["name"] == "作者"
    info = _info()
    expected = {info.video_url, info.cover_url, info.music_url, info.images[0].url,
                info.images[0].live_photo_url, info.formats[0].url, SHARE}
    assert set(data["sig"]) == expected
    # 签名是前端调 /api/proxy、/api/prepare 的凭证，必须能被验过
    assert all(net.verify(u, s) for u, s in data["sig"].items())
    assert "edge" not in data
    assert recorded == [("parse", {"source": "douyin", "ok": True, "reason": "", "ms": pytest.approx(0, abs=5000)})]


def test_share_text_is_reduced_to_its_link(client, calls, recorded):
    text = f"7.43 复制打开抖音，看看【作者的作品】好看 {SHARE} Mvs:/ 06/18"
    assert client.get("/api/parse", params={"url": text}).json()["code"] == 200
    assert calls["urls"] == [SHARE]


def test_image_note_without_video_still_succeeds(client, calls, recorded):
    # 图文笔记没有 video_url / music_url，空串不能混进签名表
    calls["result"] = _info(video_url="", music_url="", formats=[])
    data = client.get("/api/parse", params={"url": SHARE}).json()["data"]
    assert "" not in data["sig"]
    assert data["images"][0]["url"] in data["sig"]


def test_success_is_cached(client, calls, recorded):
    first = client.get("/api/parse", params={"url": SHARE}).json()
    second = client.get("/api/parse", params={"url": SHARE}).json()
    assert first == second
    assert len(calls["urls"]) == 1
    assert recorded[1] == ("parse", {"source": "douyin", "ok": True, "reason": "cache"})


def test_edge_image_urls_only_for_whitelisted_cdns(client, calls, recorded, monkeypatch):
    monkeypatch.setattr(relay, "RELAY_URL", "https://edge.example.com/relay")
    monkeypatch.setattr(relay, "RELAY_TOKEN", "tok")
    monkeypatch.setattr(relay, "EDGE_IMG", True)
    calls["result"] = _info(cover_url="https://unknown-cdn.example.net/c.jpg")

    data = client.get("/api/parse", params={"url": SHARE}).json()["data"]

    img = data["images"][0]["url"]
    assert set(data["edge"]) == {img}
    edge = httpx.URL(data["edge"][img])
    assert str(edge.copy_with(query=None)) == "https://edge.example.com/img"
    exp = int(edge.params["e"])
    # 结果会缓存 PARSE_CACHE_SECONDS，边缘签名要比缓存活得久
    assert exp > time.time() + web.cconfig.PARSE_CACHE_SECONDS
    want = hmac.new(b"tok", f"img\n{exp}\n{img}".encode(), hashlib.sha256).hexdigest()[:32]
    assert edge.params["s"] == want and edge.params["url"] == img


# --------------------------------------------------------------------------- 失败路径


@pytest.mark.parametrize("exc, code, reason", [
    (ParseError("deleted"), 500, "deleted"),
    (asyncio.TimeoutError(), 504, "timeout"),
    (RuntimeError("HTTP 412 Precondition Failed"), 500, "blocked"),
    (RuntimeError("莫名其妙"), 500, "parse"),
])
def test_failure_reports_reason_and_logs_link(client, calls, recorded, caplog, exc, code, reason):
    calls["result"] = exc
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        body = client.get("/api/parse", params={"url": SHARE}).json()

    assert body["code"] == code and body["reason"] == reason
    assert "data" not in body
    assert f"解析失败 url={SHARE} reason={reason}" in caplog.text
    (kind, kw), = recorded
    assert kind == "parse" and kw["ok"] is False and kw["reason"] == reason and kw["source"] == "douyin"


def test_success_does_not_log_failure(client, calls, recorded, caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        client.get("/api/parse", params={"url": SHARE})
    assert "解析失败" not in caplog.text


def test_text_without_link_is_rejected_before_parsing(client, calls, recorded):
    body = client.get("/api/parse", params={"url": "就一段话，没有链接"}).json()
    assert body == {"code": 400, "msg": "没有找到链接，请粘贴完整的分享内容", "reason": "unsupported"}
    assert calls["urls"] == []


def test_internal_address_is_rejected_before_parsing(client, calls, recorded, monkeypatch):
    monkeypatch.setattr(web, "is_safe_url_async", net.is_safe_url_async)
    body = client.get("/api/parse", params={"url": "http://127.0.0.1:8000/x"}).json()
    assert body["code"] == 400 and body["reason"] == "unsupported"
    assert calls["urls"] == []


def test_parse_rate_limit(client, calls, recorded, monkeypatch):
    monkeypatch.setattr(limits.parse_limit, "burst", 2)
    codes = [client.get("/api/parse", params={"url": SHARE}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# --------------------------------------------------------------------------- 转发类接口不能变成开放代理


def test_proxy_rejects_unsigned_url(client):
    r = client.get("/api/proxy", params={"url": "https://example.com/a.mp4", "sig": "0" * 32})
    assert r.status_code == 403


def test_proxy_rejects_signed_internal_url(client):
    # 签名对了也不行：解析器万一吐出内网地址，转发前还有一道 SSRF 检查
    url = "http://169.254.169.254/latest/meta-data/"
    r = client.get("/api/proxy", params={"url": url, "sig": net.sign(url)})
    assert r.status_code == 400


def test_prepare_rejects_unsigned_url(client):
    r = client.post("/api/prepare", json={"url": "https://example.com/a.mp4", "sig": "bad"})
    assert r.status_code == 403


def test_live_rejects_unsigned_items(client):
    item = {"image_url": "https://example.com/a.jpg", "video_url": "https://example.com/a.mp4"}
    r = client.post("/api/live", json={"items": [item]})
    assert r.status_code == 403
