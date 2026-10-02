"""边缘中继的 httpx transport：回程压缩的解码、边缘取图地址。不碰外网。"""

import base64
import gzip
import json
import time

import httpx
import pytest

from parse_video_py.convert import relay

PAGE = "<html>" + "小红书笔记 " * 2000 + "</html>"


def _transport(handler) -> relay.RelayTransport:
    tr = relay.RelayTransport("https://edge.example.com/relay", "tok")
    tr._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return tr


def _relay_reply(body: bytes, **extra: str) -> httpx.Response:
    pairs = [["content-type", "text/html; charset=utf-8"]]
    headers = {"x-relay-status": "200", "x-relay-headers": base64.b64encode(json.dumps(pairs).encode()).decode()}
    return httpx.Response(200, content=body, headers={**headers, **extra})


async def _get(tr: relay.RelayTransport) -> httpx.Response:
    async with httpx.AsyncClient(transport=tr) as client:
        return await client.get("https://www.xiaohongshu.com/explore/1")


async def test_asks_for_gzip_and_decodes_it():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return _relay_reply(gzip.compress(PAGE.encode()), **{"x-relay-encoding": "gzip"})

    resp = await _get(_transport(handler))
    assert seen["x-relay-accept"] == "gzip"
    assert resp.text == PAGE and resp.headers["content-type"].startswith("text/html")


async def test_old_relay_without_compression_still_works():
    resp = await _get(_transport(lambda request: _relay_reply(PAGE.encode())))
    assert resp.text == PAGE


async def test_truncated_gzip_is_a_network_error():
    # 中继边收边压边发，上游断在半路时 gzip 尾巴是缺的：按网络问题报，别当成「页面结构变了」
    cut = gzip.compress(PAGE.encode())[:-20]
    tr = _transport(lambda request: _relay_reply(cut, **{"x-relay-encoding": "gzip"}))
    with pytest.raises(httpx.ReadError, match="中继"):
        await _get(tr)


def test_edge_image_url_is_stable_within_the_hour(monkeypatch):
    monkeypatch.setattr(relay, "RELAY_URL", "https://edge.example.com/relay")
    monkeypatch.setattr(relay, "RELAY_TOKEN", "tok")
    img = "https://sns-img-hw.xhscdn.com/abc"
    hour = 1_800_000_000 // 3600 * 3600
    monkeypatch.setattr(time, "time", lambda: hour + 10)
    first = relay.edge_img_url(img, 600)
    monkeypatch.setattr(time, "time", lambda: hour + 3500)
    assert relay.edge_img_url(img, 600) == first, "同一小时里地址要一样，浏览器缓存才用得上"
    # 不管落在一小时里的哪一秒，至少还能用 ttl 秒
    assert int(httpx.URL(first).params["e"]) >= hour + 3500 + 600
