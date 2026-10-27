"""/api/parse 的成功 / 失败两条路，以及转发类接口的签名、SSRF 拦截。

平台解析器一律换成假的，不碰网络。7b42b30 那次全站 500 就是因为只测了失败路径，
所以成功路径在这里必须有。
"""

import asyncio
import hashlib
import hmac
import logging
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from qiuyuantu import stats, web
from qiuyuantu.convert import config, ffmpeg, net, relay
from qiuyuantu.parser.base import FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.web import limits
from qiuyuantu.web import parse as parse_api
from qiuyuantu.web import proxy as proxy_api

SHARE = "https://v.douyin.com/abc123/"


def _info(**kw) -> VideoInfo:
    base = {
        "video_url": "https://v3-web.douyinvod.com/play/1.mp4",
        "cover_url": "https://p3-sign.douyinpic.com/cover.jpeg",
        "title": "标题",
        "music_url": "https://sf3-cdn.douyinstatic.com/music.mp3",
        "images": [
            ImgInfo(
                url="https://p3-sign.douyinpic.com/1.webp", live_photo_url="https://v3-web.douyinvod.com/live/1.mp4"
            )
        ],
        "author": VideoAuthor(name="作者"),
        "source": "douyin",
        "formats": [FormatInfo(label="1080p", url="https://v3-web.douyinvod.com/play/1080.mp4")],
    }
    return VideoInfo(**{**base, **kw})


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

    monkeypatch.setattr(parse_api, "parse_video_share_url", fake_parse)
    monkeypatch.setattr(parse_api, "is_safe_url_async", always_safe)
    return state


@pytest.fixture
def recorded(monkeypatch):
    rows = []
    monkeypatch.setattr(stats, "TOKEN", "test-token")
    monkeypatch.setattr(stats, "record", lambda kind, ip, **kw: rows.append((kind, kw)))
    return rows


@pytest.fixture
def client(monkeypatch):
    parse_api.cache.clear()
    for rl in (limits.parse_limit, limits.proxy_limit, limits.job_limit, limits.parse_upstream_global):
        monkeypatch.setattr(rl, "_buckets", {})
    monkeypatch.setattr(stats, "_recent_downloads", {})
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
    expected = {
        info.video_url,
        info.cover_url,
        info.music_url,
        info.images[0].url,
        info.images[0].live_photo_url,
        info.formats[0].url,
        SHARE,
    }
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


@pytest.mark.parametrize(
    ("source", "link", "cover"),
    [
        ("netease", "https://music.163.com/playlist?id=14005122656", "https://p1.music.126.net/hash/cover.jpg"),
        ("qqmusic", "https://y.qq.com/n/ryqq_v2/playlist/42", "https://qpic.y.qq.com/music_cover/hash/1000"),
    ],
)
def test_music_cover_json_and_progress_apis_keep_signed_image_downloads(client, calls, recorded, source, link, cover):
    calls["result"] = VideoInfo(
        video_url="", cover_url=cover, title="音乐封面", source=source, images=[ImgInfo(url=cover)], page_url=link
    )
    body = client.get("/api/parse", params={"url": link}).json()
    assert body["code"] == 200
    data = body["data"]
    assert data["source"] == source and data["images"] == [{"url": cover, "live_photo_url": ""}]
    assert not data["video_url"] and not data["music_url"] and not data["formats"]
    assert net.verify(cover, data["sig"][cover])
    response = client.get("/api/parse/stream", params={"url": link})
    assert response.status_code == 200 and '"type": "result"' in response.text
    assert cover in response.text


def test_success_is_cached(client, calls, recorded):
    first = client.get("/api/parse", params={"url": SHARE}).json()
    second = client.get("/api/parse", params={"url": SHARE}).json()
    assert first == second
    assert len(calls["urls"]) == 1
    assert recorded[1] == ("parse", {"source": "douyin", "ok": True, "reason": "cache"})


@pytest.mark.asyncio
async def test_concurrent_parses_share_one_upstream_request(client, monkeypatch, recorded):
    entered, release = asyncio.Event(), asyncio.Event()
    urls = []

    async def parse(url):
        urls.append(url)
        entered.set()
        await release.wait()
        return _info()

    async def safe(_url):
        return True

    monkeypatch.setattr(parse_api, "parse_video_share_url", parse)
    monkeypatch.setattr(parse_api, "is_safe_url_async", safe)
    monkeypatch.setattr(limits.parse_upstream_global, "burst", 1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as http:
        first = asyncio.create_task(http.get("/api/parse", params={"url": SHARE}))
        await asyncio.wait_for(entered.wait(), 1)
        rest = [asyncio.create_task(http.get("/api/parse", params={"url": SHARE})) for _ in range(5)]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, *rest)
    assert urls == [SHARE] and all(r.status_code == 200 and r.json()["code"] == 200 for r in results)
    assert len(recorded) == 6 and not parse_api._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_all", [False, True])
async def test_shared_parse_cancellation_tracks_remaining_waiters(client, monkeypatch, cancel_all):
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def parse(_url):
        entered.set()
        try:
            await release.wait()
            return {"code": 200}
        finally:
            stopped.set()

    monkeypatch.setattr(parse_api, "_parse", parse)
    first = asyncio.create_task(parse_api._shared_parse(SHARE))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(parse_api._shared_parse(SHARE))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not stopped.is_set() and not second.done()
    if cancel_all:
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
    else:
        release.set()
        assert await second == {"code": 200}
    assert stopped.is_set() and not parse_api._pending


def test_youtube_failure_is_counted_under_youtube(client, calls, recorded):
    calls["result"] = ParseError("blocked")
    body = client.get("/api/parse", params={"url": "https://youtu.be/example"}).json()
    assert body["reason"] == "blocked"
    assert recorded[0][1]["source"] == "youtube" and not recorded[0][1]["ok"]


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
    assert exp > time.time() + config.PARSE_CACHE_SECONDS
    want = hmac.new(b"tok", f"img\n{exp}\n{img}".encode(), hashlib.sha256).hexdigest()[:32]
    assert edge.params["s"] == want and edge.params["url"] == img


# --------------------------------------------------------------------------- 失败路径


@pytest.mark.parametrize(
    "exc, code, reason",
    [
        (ParseError("deleted"), 500, "deleted"),
        (asyncio.TimeoutError(), 504, "timeout"),
        (RuntimeError("HTTP 412 Precondition Failed"), 500, "blocked"),
        (RuntimeError("莫名其妙"), 500, "parse"),
    ],
)
def test_failure_reports_reason_and_logs_link(client, calls, recorded, caplog, exc, code, reason):
    calls["result"] = exc
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        body = client.get("/api/parse", params={"url": SHARE}).json()

    assert body["code"] == code and body["reason"] == reason
    assert "data" not in body
    assert f"解析失败 url={SHARE} reason={reason}" in caplog.text
    ((kind, kw),) = recorded
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
    monkeypatch.setattr(parse_api, "is_safe_url_async", net.is_safe_url_async)
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


def test_animated_cover_requires_signature_and_uses_separate_preparation_cache(client, monkeypatch):
    from qiuyuantu.convert import jobs, store, tasks
    from qiuyuantu.web import media

    url = "https://p1.music.126.net/cover.jpg"
    body = {"url": url, "image": True, "sig": "bad"}
    assert client.post("/api/prepare", json=body).status_code == 403
    body["sig"] = net.sign(url)
    assert client.post("/api/prepare", json=body | {"page_url": url}).status_code == 400
    seen = {}

    async def safe(_):
        return True

    async def fetch(job, **kwargs):
        seen.update(kwargs)

    def start(kind, fn, ip, source_id):
        job = jobs.Job(id="animation-api", type=kind, source_id=source_id)
        seen["run"] = lambda: fn(job)
        return job

    monkeypatch.setattr(media, "is_safe_url_async", safe)
    monkeypatch.setattr(media, "_start_job", start)
    monkeypatch.setattr(tasks, "fetch_source", fetch)
    response = client.post("/api/prepare", json=body)
    assert response.status_code == 200, response.text
    asyncio.run(seen.pop("run")())
    assert response.json()["source_id"] == store.source_id_for(url, "animation")
    assert response.json()["source_id"] != store.source_id_for(url)
    assert seen["image"] is True and seen["url"] == url and not seen["page_url"]


def test_live_rejects_unsigned_items(client):
    item = {"image_url": "https://example.com/a.jpg", "video_url": "https://example.com/a.mp4"}
    r = client.post("/api/live", json={"items": [item]})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "filename, saved_as",
    [("clip.MOV", ".mov"), ("a.webm", ".webm"), ("evil.m3u8", ".mp4"), ("x.ffconcat", ".mp4"), ("noext", ".mp4")],
)
def test_upload_keeps_only_video_extensions(client, monkeypatch, tmp_path, filename, saved_as):
    # 用户给的扩展名不能原样用：.m3u8 会让 ffmpeg 按 HLS 打开，顺着里面的路径去读服务器上别的文件
    seen = []

    async def fake_probe(path):
        seen.append(Path(path).suffix)
        return ffmpeg.ProbeInfo(duration=2, width=320, height=240, fps=15)

    monkeypatch.setattr(ffmpeg, "probe", fake_probe)
    monkeypatch.setattr(limits.upload_limit, "_buckets", {})
    r = client.post("/api/upload", files={"file": (filename, b"#EXTM3U\n/app/data/secret.mp4\n", "video/mp4")})
    assert r.status_code == 200 and seen == [saved_as]


def test_edge_media_and_csp_when_enabled(client, calls, recorded, monkeypatch):
    monkeypatch.setattr(relay, "RELAY_URL", "https://edge.example.com/relay")
    monkeypatch.setattr(relay, "RELAY_TOKEN", "tok")
    monkeypatch.setattr(relay, "EDGE_MEDIA", True)
    data = client.get("/api/parse", params={"url": SHARE}).json()["data"]
    info = _info()
    media = {info.video_url, info.music_url, info.formats[0].url, info.images[0].live_photo_url}
    # 视频 / 音频给边缘地址（douyinvod.com、douyinstatic.com 都在白名单里）；图片没开 EDGE_IMG 就不给
    assert set(data["edge"]) == media
    assert all(httpx.URL(u).path == "/media" for u in data["edge"].values())


def test_download_hit_counts_only_signed_urls(client, recorded):
    url = "https://v3-web.douyinvod.com/play/1.mp4"
    # 假签名明确拒掉（以前回 ok:true 但不记，看着像刷成功了）
    assert client.post("/api/download-hit", json={"url": url, "sig": "bad"}).status_code == 403
    assert client.post("/api/download-hit", json={"url": url, "sig": net.sign(url)}).status_code == 200
    assert recorded == [("download", {"source": "douyinvod.com"})]


def test_segmented_download_counts_once(client, recorded, monkeypatch):
    # 下载器把一个文件切成多段 Range 请求，每段都带 download=1：只算一次下载
    async def upstream(url, headers):
        return httpx.Response(206, stream=httpx.ByteStream(b"x"), headers={"content-type": "video/mp4"})

    async def always_safe(url):
        return True

    monkeypatch.setattr(proxy_api, "_open_upstream", upstream)
    monkeypatch.setattr(proxy_api, "is_safe_url_async", always_safe)
    url = "https://v3-web.douyinvod.com/play/1.mp4"
    params = {"url": url, "sig": net.sign(url), "download": 1}
    for start in (0, 1 << 20, 2 << 20):
        assert client.get("/api/proxy", params=params, headers={"range": f"bytes={start}-"}).status_code == 206
    client.post("/api/download-hit", json={"url": url, "sig": net.sign(url)})
    assert recorded == [("download", {"source": "douyinvod.com"})]
    # 换一个文件照常算
    other = "https://v3-web.douyinvod.com/play/2.mp4"
    client.post("/api/download-hit", json={"url": other, "sig": net.sign(other)})
    assert len(recorded) == 2


def test_global_parse_cap_counts_only_upstream_fetches(client, calls, monkeypatch):
    # 全站总量只在真去平台抓时扣：缓存命中、不支持的链接不占
    monkeypatch.setattr(limits.parse_upstream_global, "burst", 1)
    first = "https://v.douyin.com/a/"
    assert client.get("/api/parse", params={"url": first}).json()["code"] == 200
    assert client.get("/api/parse", params={"url": first}).json()["code"] == 200  # 缓存
    assert client.get("/api/parse", params={"url": "没有链接"}).json()["code"] == 400
    r = client.get("/api/parse", params={"url": "https://v.douyin.com/b/"})
    assert r.status_code == 429 and "太多" in r.json()["detail"]
    assert calls["urls"] == [first]


def test_proxy_streams_have_a_global_cap(client, monkeypatch):
    async def upstream(url, headers):
        return httpx.Response(206, stream=httpx.ByteStream(b"x"), headers={"content-type": "video/mp4"})

    async def always_safe(url):
        return True

    monkeypatch.setattr(proxy_api, "_open_upstream", upstream)
    monkeypatch.setattr(proxy_api, "is_safe_url_async", always_safe)
    monkeypatch.setattr(limits, "proxy_streams_global", limits.Concurrency("下载", 0, busy="现在下载的人太多了"))
    url = "https://v3-web.douyinvod.com/play/1.mp4"
    r = client.get("/api/proxy", params={"url": url, "sig": net.sign(url)})
    assert r.status_code == 429 and "太多" in r.json()["detail"]
    # 全站满了被拒，按 IP 的那份也要还回去，不然这个 IP 会一直被卡
    assert limits.proxy_streams._active == {}


def test_proxy_gives_slot_back_when_client_leaves_before_first_byte(monkeypatch):
    """上游第一块数据还没到用户就关了页面：收尾是在已取消的状态下跑的，名额和上游连接都得还回去。
    以前先 await 关连接再还名额，await 一挂起就被取消，名额就漏了，漏满了这个 IP / 全站一直 429。"""
    closed = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.5)
            yield b"x"

        async def aclose(self):
            await asyncio.sleep(0)
            closed.append(True)

    async def upstream(url, headers):
        return httpx.Response(200, stream=SlowStream(), headers={"content-type": "video/mp4"})

    async def always_safe(url):
        return True

    monkeypatch.setattr(proxy_api, "_open_upstream", upstream)
    monkeypatch.setattr(proxy_api, "is_safe_url_async", always_safe)
    monkeypatch.setattr(limits.proxy_limit, "_buckets", {})
    url = "https://v3-web.douyinvod.com/play/1.mp4"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/proxy",
        "raw_path": b"/api/proxy",
        "query_string": str(httpx.QueryParams({"url": url, "sig": net.sign(url)})).encode(),
        "root_path": "",
        # 带 gzip 时 GZipMiddleware 会把响应头扣到第一块数据才发，正好是线上出事的情形
        "headers": [(b"host", b"t"), (b"accept-encoding", b"gzip")],
        "client": ("203.0.113.9", 1),
        "server": ("t", 80),
    }

    async def receive():
        await asyncio.sleep(0.05)
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    asyncio.run(web.app(scope, receive, send))  # 也不该再抛 "No response returned."
    assert limits.proxy_streams._active == {} and limits.proxy_streams_global._active == {}
    assert closed == [True]


_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize(
    ("headers", "download", "status"),
    [
        ({"User-Agent": _CHROME, "Referer": "https://ynvan.com/"}, 0, 200),  # 网页里播放
        ({"User-Agent": _CHROME}, 1, 200),  # 手机浏览器自带的下载器接手，不带 Referer
        ({"User-Agent": "BackgroundShortcutRunner/5037.111"}, 0, 200),  # 快捷指令
        ({"User-Agent": _CHROME}, 0, 403),  # 脚本冒充浏览器直接拉（2026-10-18）
    ],
)
def test_proxy_refuses_browser_ua_outside_the_page(client, monkeypatch, headers, download, status):
    async def upstream(url, headers):
        return httpx.Response(200, stream=httpx.ByteStream(b"x"), headers={"content-type": "video/mp4"})

    async def always_safe(url):
        return True

    monkeypatch.setattr(proxy_api, "_open_upstream", upstream)
    monkeypatch.setattr(proxy_api, "is_safe_url_async", always_safe)
    url = "https://video.twimg.com/amplify_video/1/vid/1.mp4"
    params = {"url": url, "sig": net.sign(url), "download": download}
    assert client.get("/api/proxy", params=params, headers=headers).status_code == status


def test_proxy_sends_headers_remembered_at_parse_time(client, calls, monkeypatch):
    """TikTok 的视频地址要带 yt-dlp 解析时拿到的 cookie，名单里没有的 CDN 补平台自己的 Referer；
    cookie 不发给页面。"""
    video, cover = "https://v19-webapp-prime.us.tiktok.com/video/1", "https://img.unlisted-cdn.net/c.jpg"
    calls["result"] = _info(
        video_url=video,
        cover_url=cover,
        page_url="https://www.tiktok.com/@a/video/1",
        video_headers={"Referer": "https://www.tiktok.com/@a/video/1", "Cookie": "tt_chain_token=abc"},
    )
    sent = {}

    async def upstream(url, headers):
        sent[url] = headers
        return httpx.Response(200, stream=httpx.ByteStream(b"x"), headers={"content-type": "video/mp4"})

    async def always_safe(url):
        return True

    monkeypatch.setattr(proxy_api, "_open_upstream", upstream)
    monkeypatch.setattr(proxy_api, "is_safe_url_async", always_safe)
    data = client.get("/api/parse", params={"url": "https://www.tiktok.com/@a/video/1"}).json()["data"]
    assert data["video_headers"] == {"Referer": "https://www.tiktok.com/@a/video/1"}
    for url in (video, cover, data["images"][0]["url"]):
        client.get("/api/proxy", params={"url": url, "sig": data["sig"][url]})
    assert sent[video]["Cookie"] == "tt_chain_token=abc"
    assert sent[cover]["Referer"] == "https://www.tiktok.com/"
    assert sent[data["images"][0]["url"]]["Referer"] == "https://www.douyin.com/"  # 名单里有的照名单
