"""实况打包拉原图：国内图片 CDN 直连握不上就走中继，失败过的 CDN 一段时间内直接走中继。"""

import asyncio

import httpx
import pytest

from parse_video_py.convert import relay, tasks

DOUYIN_IMG = "https://p95-zjwztc-sign.douyinpic.com/tos-cn-i-0813c000-ce/abc~tplv-dy-aweme-images:q75.jpeg"
VIDEO = "https://v95-se-zjwztc-default.365yg.com/x/video/tos/cn/tos-cn-ve-15/abc/"


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """直连一律握手超时、中继一律成功的假下载，记下每次走的哪条路。"""
    log = []

    async def fake_fetch_once(url, dest, headers, limit, *, via_relay=False, connect_timeout=30):
        log.append(("relay" if via_relay else "direct", connect_timeout if not via_relay else None))
        if not via_relay:
            raise httpx.ConnectTimeout("handshake timed out")

    monkeypatch.setattr(tasks, "_fetch_once", fake_fetch_once)
    monkeypatch.setattr(tasks, "_RELAY_FIRST", {})
    monkeypatch.setattr(relay, "RELAY_URL", "https://edge.example.com/relay")
    monkeypatch.setattr(relay, "RELAY_TOKEN", "tok")
    return log


def _fetch(url, dest):
    asyncio.run(tasks._fetch_bytes(url, dest))


def test_cn_image_falls_back_to_relay_then_goes_relay_first(calls, tmp_path):
    _fetch(DOUYIN_IMG, tmp_path / "1")
    # 直连用短一点的连接超时，失败就换中继
    assert calls == [("direct", 10), ("relay", None)]
    calls.clear()
    # 同一条作品的图分在不同子域名上，坏的是整个 CDN 的跨境链路
    _fetch(DOUYIN_IMG.replace("p95-zjwztc-sign", "p5-ex-gddgtc-sign"), tmp_path / "2")
    assert calls == [("relay", None)], "同一个 CDN 刚失败过，不该再白等一轮直连超时"


def test_video_and_other_hosts_never_use_relay(calls, tmp_path):
    for url in (VIDEO, "https://pbs.twimg.com/media/a.jpg"):
        with pytest.raises(httpx.ConnectTimeout):
            _fetch(url, tmp_path / "v")
    assert calls == [("direct", 30), ("direct", 30)]


def test_no_relay_configured_keeps_direct_error(calls, tmp_path, monkeypatch):
    monkeypatch.setattr(relay, "RELAY_TOKEN", "")
    with pytest.raises(httpx.ConnectTimeout):
        _fetch(DOUYIN_IMG, tmp_path / "1")
    assert calls == [("direct", 30)]
