"""YouTube 抽查「确认不是机器人」时换备用出口（WARP）重试，直链也走同一个出口。"""

import asyncio

import pytest

from parse_video_py import utils
from parse_video_py.convert import net
from parse_video_py.parser import ytdlp

BOT = "ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser"


@pytest.fixture
def warp(monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")
    monkeypatch.setattr(net, "_FALLBACK_URLS", {})


@pytest.mark.parametrize(
    ("msg", "configured", "expected"),
    [
        (BOT, True, True),
        ("Sign in to confirm your age", True, False),  # 年龄限制换出口也没用
        (BOT, False, False),
    ],
)
def test_wants_fallback(monkeypatch, msg, configured, expected):
    if configured:
        monkeypatch.setenv("PARSE_VIDEO_FALLBACK_PROXY", "http://warp:1080")
    else:
        monkeypatch.delenv("PARSE_VIDEO_FALLBACK_PROXY", raising=False)
    assert utils.wants_fallback(RuntimeError(msg)) is expected


def test_parse_retries_through_fallback_and_remembers_urls(warp, monkeypatch):
    calls = []

    def extract(url, proxy=None):
        calls.append(proxy)
        if proxy is None:
            raise RuntimeError(BOT)
        return {
            "title": "t",
            "duration": 3,
            "formats": [
                {
                    "url": "https://rr1.googlevideo.com/a",
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "ext": "mp4",
                    "height": 360,
                }
            ],
        }

    monkeypatch.setattr(ytdlp.YtDlp, "_extract", staticmethod(extract))
    info = asyncio.run(ytdlp.YtDlp().parse_share_url("https://www.youtube.com/watch?v=abc"))
    assert calls == [None, "http://warp:1080"]
    assert info.video_url == "https://rr1.googlevideo.com/a"
    assert net.proxy_for_url(info.video_url) == "http://warp:1080"
    assert net.proxy_for_url("https://rr1.googlevideo.com/other") != "http://warp:1080"


def test_other_errors_are_not_retried(warp, monkeypatch):
    calls = []

    def extract(url, proxy=None):
        calls.append(proxy)
        raise RuntimeError("Video unavailable")

    monkeypatch.setattr(ytdlp.YtDlp, "_extract", staticmethod(extract))
    with pytest.raises(RuntimeError):
        asyncio.run(ytdlp.YtDlp().parse_share_url("https://www.youtube.com/watch?v=abc"))
    assert calls == [None]


def test_fallback_proxy_host_is_trusted_by_ssrf_guard(warp):
    assert "warp" in net._trusted_hosts()
