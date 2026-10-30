"""YouTube 运行依赖、解析到下载的出口一致性，以及风控 / HTTP 下载失败的回归。"""

import sys
from types import SimpleNamespace

import pytest

from qiuyuantu import utils
from qiuyuantu.convert import config, fetch, net
from qiuyuantu.parser.errors import classify
from qiuyuantu.parser.ytdlp import YtDlp

PAGE = "https://www.youtube.com/watch?v=example"
MEDIA = "https://rr1.googlevideo.com/videoplayback"


@pytest.mark.parametrize("name", ["deno", "node", None])
def test_js_runtime_on_path(monkeypatch, name):
    monkeypatch.setitem(sys.modules, "deno", None)
    monkeypatch.setattr(config.shutil, "which", lambda exe: f"/bin/{exe}" if exe == name else None)
    expected = {"js_runtimes": {name: {"path": f"/bin/{name}"}}} if name else {}
    assert config.ytdlp_runtime_opts() == expected


def test_bundled_deno_works_without_activating_venv(monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    monkeypatch.setitem(sys.modules, "deno", SimpleNamespace(find_deno_bin=lambda: "/venv/package/deno"))
    assert config.ytdlp_runtime_opts() == {"js_runtimes": {"deno": {"path": "/venv/package/deno"}}}


@pytest.fixture
def routes(monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_PROXY_YOUTUBE", "http://youtube-proxy:8080")
    monkeypatch.setenv("PARSE_VIDEO_PROXY", "http://general-proxy:8080")
    monkeypatch.setenv("PARSE_VIDEO_PROXY_CN", "http://cn-proxy:8080")
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")
    monkeypatch.setattr(net, "_FALLBACK_URLS", {})
    monkeypatch.setattr(fetch, "_sized_format", lambda *args: "b")
    monkeypatch.setattr(fetch.ffmpeg, "ffmpeg_dir", lambda: "/bin")


def test_youtube_page_and_media_use_dedicated_proxy(routes, tmp_path):
    assert utils.youtube_proxy(PAGE) == "http://youtube-proxy:8080"
    assert net.proxy_for_url(MEDIA) == "http://youtube-proxy:8080"
    opts = fetch._ytdlp_opts(PAGE, "b", tmp_path, "v", fit=False)
    assert opts["proxy"] == "http://youtube-proxy:8080"
    assert "youtube-proxy" in net._trusted_hosts()
    # 不能在域名参数里或相似域名上误用专用出口。
    assert utils.youtube_proxy("https://example.com/?next=youtube.com") is None
    assert utils.youtube_proxy("https://notyoutube.com/v") is None


def test_parse_and_download_pass_same_runtime_and_proxy(routes, monkeypatch, tmp_path):
    runtime = {"js_runtimes": {"deno": {"path": "/package/deno"}}}
    monkeypatch.setattr(config, "ytdlp_runtime_opts", lambda: runtime)
    seen = []

    class Downloader:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def extract_info(self, *_args, **_kw):
            return {"title": "example"}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Downloader))
    YtDlp._extract(PAGE)
    download = fetch._ytdlp_opts(PAGE, "b", tmp_path, "v", fit=False)
    for opts in (seen[0], download):
        assert opts["js_runtimes"] == runtime["js_runtimes"]
        assert opts["proxy"] == "http://youtube-proxy:8080"


def test_cn_proxy_still_takes_priority_over_general_proxy(routes, tmp_path):
    opts = fetch._ytdlp_opts("https://www.bilibili.com/video/BV1", "b", tmp_path, "v", fit=False)
    assert opts["proxy"] == "http://cn-proxy:8080"


def test_download_reuses_parse_fallback_route(routes, tmp_path):
    net.remember_fallback([PAGE, MEDIA])
    opts = fetch._ytdlp_opts(PAGE, "b", tmp_path, "v", fit=False)
    assert opts["proxy"] == net.proxy_for_url(MEDIA) == "http://warp:1080"


def test_expired_fallback_route_is_not_reused(routes, monkeypatch):
    monkeypatch.setattr(net.time, "monotonic", lambda: net._FALLBACK_TTL + 1)
    net._FALLBACK_URLS[MEDIA] = 0
    assert net.proxy_for_url(MEDIA) == "http://youtube-proxy:8080"
    assert MEDIA not in net._FALLBACK_URLS


@pytest.mark.parametrize("code", [403, 429])
def test_http_block_retries_only_youtube(routes, code):
    error = RuntimeError(f"HTTP Error {code}: server refused request")
    assert utils.wants_fallback(error, PAGE)
    assert not utils.wants_fallback(error, "https://www.instagram.com/p/example/")


def test_bot_check_is_blocked_not_a_request_for_account_cookies():
    message = "Sign in to confirm you’re not a bot. Use --cookies for authentication"
    assert classify(RuntimeError(message)).reason == "blocked"
    assert classify(RuntimeError("Sign in to confirm your age")).reason == "login"


def test_http_download_failure_retries_once_on_fallback(routes, monkeypatch, tmp_path):
    calls = []

    def run(opts, _url, out_dir, stem):
        calls.append(opts.get("proxy"))
        if len(calls) == 1:
            raise RuntimeError("HTTP Error 403: Forbidden")
        path = out_dir / f"{stem}.mp4"
        path.write_bytes(b"media")
        return {"requested_downloads": [{"filepath": str(path)}]}

    monkeypatch.setattr(fetch, "_ytdlp_run", run)
    path = fetch.ytdlp_download(PAGE, "b", tmp_path, "v", report=lambda *_: None, cancelled=lambda: False)
    assert path.read_bytes() == b"media"
    assert calls == ["http://youtube-proxy:8080", "http://warp:1080"]


def test_already_using_fallback_does_not_retry_same_failed_route(routes, monkeypatch, tmp_path):
    net.remember_fallback([PAGE])
    calls = []

    def run(opts, *_args):
        calls.append(opts.get("proxy"))
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(fetch, "_ytdlp_run", run)
    with pytest.raises(RuntimeError, match="429"):
        fetch.ytdlp_download(PAGE, "b", tmp_path, "v", report=lambda *_: None, cancelled=lambda: False)
    assert calls == ["http://warp:1080"]


def _recording_ytdlp(monkeypatch):
    seen = []

    class Downloader:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def extract_info(self, *_args, **_kw):
            return {"title": "example"}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Downloader))
    return seen


def test_fallback_route_uses_readable_clients_for_parse_and_download(routes, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "POT_URL", "http://bgutil:4416")
    seen = _recording_ytdlp(monkeypatch)
    YtDlp._extract(PAGE, "http://warp:1080")
    net.remember_fallback([PAGE])
    download = fetch._ytdlp_opts(PAGE, "b", tmp_path, "v", fit=False)
    for opts in (seen[0], download):
        # 解析与下载的客户端一致，format_id 才能对上；PO Token 参数保留。
        assert opts["extractor_args"]["youtube"]["player_client"] == ["tv_simply"]
        assert opts["extractor_args"]["youtubepot-bgutilhttp"] == {"base_url": ["http://bgutil:4416"]}


def test_default_route_and_other_sites_keep_ytdlp_clients(routes, monkeypatch, tmp_path):
    seen = _recording_ytdlp(monkeypatch)
    YtDlp._extract(PAGE)
    YtDlp._extract("https://vimeo.com/1", "http://warp:1080")
    opts = fetch._ytdlp_opts(PAGE, "b", tmp_path, "v", fit=False)
    for item in (*seen, opts):
        assert "youtube" not in item.get("extractor_args", {})


def test_download_retry_on_fallback_switches_clients(routes, monkeypatch, tmp_path):
    clients = []

    def run(opts, _url, out_dir, stem):
        clients.append((opts.get("extractor_args") or {}).get("youtube", {}).get("player_client"))
        if len(clients) == 1:
            raise RuntimeError("HTTP Error 403: Forbidden")
        path = out_dir / f"{stem}.mp4"
        path.write_bytes(b"media")
        return {"requested_downloads": [{"filepath": str(path)}]}

    monkeypatch.setattr(fetch, "_ytdlp_run", run)
    fetch.ytdlp_download(PAGE, "b", tmp_path, "v", report=lambda *_: None, cancelled=lambda: False)
    assert clients == [None, ["tv_simply"]]


def test_empty_fallback_client_setting_keeps_ytdlp_defaults(routes, monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_FALLBACK_CLIENTS", [])
    seen = _recording_ytdlp(monkeypatch)
    YtDlp._extract(PAGE, "http://warp:1080")
    assert "youtube" not in seen[0].get("extractor_args", {})
