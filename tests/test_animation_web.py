"""特殊素材仍需签名，文件响应不成为脚本源，上传选择复用后台配额。"""

import httpx
import pytest
from fastapi.testclient import TestClient

from qiuyuantu import web
from qiuyuantu.convert import animation_uploads, net
from qiuyuantu.convert.jobs import Job
from qiuyuantu.parser.base import MediaAsset, VideoInfo
from qiuyuantu.web import media, parse, proxy


@pytest.fixture
def client():
    parse.cache.clear()
    return TestClient(web.app)


async def safe(*args):
    return True


def test_post_parse_and_stream_keep_get_compatibility(client, monkeypatch):
    calls = []

    async def extract(url):
        calls.append(url)
        return VideoInfo(
            video_url="", cover_url="", source="animation", assets=[MediaAsset(url=url, ext="svga", kind="animation")]
        )

    monkeypatch.setattr(parse, "is_safe_url_async", safe)
    monkeypatch.setattr(parse, "parse_video_share_url", extract)
    url = "https://public.example/animation.svga"
    result = client.post("/api/parse", json={"url": url})
    assert result.status_code == 200 and result.json()["code"] == 200
    assert net.verify(url, result.json()["data"]["sig"][url])
    assert client.get("/api/parse", params={"url": url}).json()["code"] == 200
    stream = client.post("/api/parse/stream", json={"url": url})
    assert "text/event-stream" in stream.headers["content-type"]
    assert '"type": "result"' in stream.text and len(calls) == 1


def test_arbitrary_public_animation_is_forced_to_download_even_if_upstream_sends_script(client, monkeypatch):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"window.untrusted = true;"

    async def upstream(url, headers):
        return httpx.Response(200, headers={"content-type": "application/javascript"}, stream=Body())

    monkeypatch.setattr(proxy, "is_safe_url_async", safe)
    monkeypatch.setattr(proxy, "_open_upstream", upstream)
    url = "https://public.example/fake.svga"
    response = client.get("/api/proxy", params={"url": url, "sig": net.sign(url)})
    assert response.status_code == 200 and response.content == b"window.untrusted = true;"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"].startswith("sandbox;")
    assert client.get("/api/proxy", params={"url": url, "sig": "bad"}).status_code == 403


def test_spine_upload_lists_choices_and_prepare_validates_them(client, monkeypatch, tmp_path):
    ident = "a" * 32
    registered = []
    upload = animation_uploads.Upload(ident, tmp_path / "uploaded.zip", "Character", ["walk", "idle"], 10**12)

    def register(path, title):
        registered.append(path.read_bytes())
        return upload

    monkeypatch.setattr(animation_uploads, "register", register)
    monkeypatch.setattr(animation_uploads, "get", lambda key: upload)
    queued = []

    def enqueue(kind, fn, ip, source_id=None):
        queued.append((kind, fn, source_id))
        return Job(id="job", type=kind, source_id=source_id)

    monkeypatch.setattr(media, "_start_job", enqueue)
    response = client.post("/api/upload", files={"file": ("character.zip", b"local bundle", "application/zip")})
    assert response.json() == {"ready": False, "animation_upload": ident, "animations": ["walk", "idle"]}
    assert registered == [b"local bundle"] and not queued
    assert client.post("/api/animation/prepare", json={"upload_id": ident, "animation": "other"}).status_code == 400
    response = client.post("/api/animation/prepare", json={"upload_id": ident, "animation": "walk"})
    assert response.json()["ready"] is False and queued[0][0] == "prepare"
    assert str(tmp_path) not in response.text


def test_animation_upload_size_limit_is_enforced_before_register(client, monkeypatch):
    monkeypatch.setattr(media.config, "MAX_UPLOAD_BYTES", 8)
    called = []
    monkeypatch.setattr(animation_uploads, "register", lambda *args: called.append(args))
    response = client.post("/api/upload", files={"file": ("huge.pag", b"x" * 9, "application/octet-stream")})
    assert response.status_code == 413 and not called


def test_truncated_zip_upload_reports_invalid_input_and_deletes_temporary_file(client, monkeypatch):
    before = set(media.config.UPLOADS_DIR.glob("*"))
    response = client.post("/api/upload", files={"file": ("broken.zip", b"PK\x03\x04truncated", "application/zip")})
    assert response.status_code == 400 and "结构无效" in response.json()["detail"]
    assert set(media.config.UPLOADS_DIR.glob("*")) == before


@pytest.mark.parametrize(
    "slug",
    ["stream-emotes", "line-stickers", "signal-stickers", "animated-emoji", "svga-to-gif", "animation-converter"],
)
def test_new_tool_pages_have_localized_real_entries_and_no_executable_inline_script(client, slug):
    zh = client.get("/" + slug, headers={"Accept-Language": "zh-CN"})
    en = client.get("/" + slug, headers={"Accept-Language": "en"})
    assert zh.status_code == en.status_code == 200
    assert 'id="paste-form"' in zh.text and 'id="file-input"' in zh.text
    assert 'lang="en"' in en.text and "<script>" not in zh.text
    assert "application/ld+json" in zh.text
    assert 'href="/' + slug + '"' in zh.text


def test_rendered_mp4_has_an_explicit_download_name(client, tmp_path):
    from qiuyuantu.convert import store

    path = tmp_path / "rendered.mp4"
    path.write_bytes(b"known video bytes")
    source = store.put(store.Source(id="rendered-test", path=str(path), title="animation", gif_path="alpha.mov"))
    assert source.view()["animation"] is True
    response = client.get("/api/source/" + source.id + "?download=1")
    assert response.status_code == 200 and response.content == path.read_bytes()
    assert "animation.mp4" in response.headers["content-disposition"]
