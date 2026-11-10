"""边缘中继的 httpx transport：回程压缩的解码、边缘取图地址。不碰外网。"""

import base64
import gzip
import hashlib
import hmac
import json
import time

import httpx
import pytest

from qiuyuantu.convert import relay

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


async def test_byte_cap_is_sent_only_when_the_caller_asks_for_it():
    # 中继收完正文才回：按范围取媒体片段的调用方给出上限，源站不理会 Range 时边缘节点读够就停
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-relay-max"))
        return _relay_reply(b"ok")

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        await client.get("https://cdn.example.com/a.mp4", headers={"Range": "bytes=0-65535"})
        await client.get("https://cdn.example.com/a.mp4", extensions={relay.MAX_BYTES: 65536})
    assert seen == [None, "65536"]


@pytest.mark.parametrize(
    "body",
    [
        gzip.compress(PAGE.encode())[:-20],  # 中继边收边压边发，上游断在半路时 gzip 尾巴是缺的
        bytes.fromhex("1f8b0800000000000003") + b"\xff\xff\xff\xff",  # 压缩数据中间坏了：抛的是 zlib.error
        b"<html>not gzip</html>",
    ],
    ids=["truncated", "corrupt-deflate", "not-gzip"],
)
async def test_broken_gzip_is_a_network_error(body):
    # 按网络问题报，别当成「页面结构变了」
    tr = _transport(lambda request: _relay_reply(body, **{"x-relay-encoding": "gzip"}))
    with pytest.raises(httpx.ReadError, match="中继"):
        await _get(tr)


class _ResetStream(httpx.AsyncByteStream):
    """响应头到了之后流被重置：边缘运行时读不了源站正文时就是这个样子。"""

    async def __aiter__(self):
        raise httpx.RemoteProtocolError("<StreamReset stream_id:1, error_code:2, remote_reset:True>")
        yield b""


def _reset_reply(status: int) -> httpx.Response:
    pairs = [["location", "https://i.y.qq.com/v8/playsong.html?songmid=abc"]] if status == 302 else []
    headers = {"x-relay-status": str(status), "x-relay-headers": base64.b64encode(json.dumps(pairs).encode()).decode()}
    return httpx.Response(200, headers={**headers, "x-relay-encoding": "gzip"}, stream=_ResetStream())


async def test_redirect_survives_a_body_the_edge_could_not_read():
    # 跳转配了个声明 gzip 的 0 字节正文：Location 在头里，照常交给上层跟跳转
    response = await _get(_transport(lambda request: _reset_reply(302)))
    assert response.status_code == 302 and response.content == b""
    assert response.headers["location"] == "https://i.y.qq.com/v8/playsong.html?songmid=abc"


async def test_broken_body_of_a_normal_page_is_still_an_error():
    with pytest.raises(httpx.RemoteProtocolError):
        await _get(_transport(lambda request: _reset_reply(200)))


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


def test_edge_media_urls_only_for_whitelisted_video_cdns(monkeypatch):
    monkeypatch.setattr(relay, "RELAY_URL", "https://edge.example.com/relay")
    monkeypatch.setattr(relay, "RELAY_TOKEN", "tok")
    video = "https://v95-se-zjwztc-default.365yg.com/x/video/tos/cn/a.mp4"
    url = httpx.URL(relay.edge_media_url(video, 3600))
    assert str(url.copy_with(query=None)) == "https://edge.example.com/media"
    exp = int(url.params["e"])
    want = hmac.new(b"tok", f"media\n{exp}\n{video}".encode(), hashlib.sha256).hexdigest()[:32]
    assert url.params["s"] == want and url.params["url"] == video
    # 图片的签名和视频的不通用：同一个地址两种签名不一样
    assert relay.edge_img_url("https://p3.douyinpic.com/a.jpeg", 3600) is not None
    assert relay.edge_media_url("https://p3.douyinpic.com/a.jpeg", 3600) is None
    assert relay.edge_media_url("https://evil.example.com/a.mp4", 3600) is None
    assert relay.edge_media_url("https://cn-hbyc-ct-01-01.bilivideo.com/upgcxcode/a.mp4", 3600)


def test_relay_client_speaks_http2():
    # 并发请求共用一条连接：HTTP/1.1 时第二个请求要另开冷连接，B站 解析多 1~2 秒
    assert relay.RelayTransport("https://edge.example.com/relay", "tok")._client._transport._pool._http2


# --------------------------------------------------------------------------- 走中继的请求不在本机查 DNS


@pytest.fixture
def no_dns(monkeypatch):
    """本机 DNS 查了哪些域名。"""
    from qiuyuantu.convert import net

    asked = []

    async def getaddrinfo(self, host, *args, **kwargs):
        asked.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(net, "_dns_cache", {})
    monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", getaddrinfo)
    return asked


def _relay_redirect(location: str) -> httpx.Response:
    pairs = [["location", location]]
    headers = {"x-relay-status": "302", "x-relay-headers": base64.b64encode(json.dumps(pairs).encode()).decode()}
    return httpx.Response(200, headers=headers)


async def test_relayed_requests_skip_local_dns_but_follow_every_hop(no_dns):
    from qiuyuantu.convert import net

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        target = request.url.params["url"]
        seen.append(target)
        if target.endswith("/short"):
            return _relay_redirect("https://www.xiaohongshu.com/explore/1")
        return _relay_reply(b"ok")

    client = net.safe_client(transport=_transport(handler), follow_redirects=True)
    r = await client.get("https://xhslink.cn/short")
    assert r.status_code == 200 and seen == ["https://xhslink.cn/short", "https://www.xiaohongshu.com/explore/1"]
    assert no_dns == []  # 连接是边缘节点发起的，本机解析出的 IP 用不上


@pytest.mark.parametrize(
    "target", ["http://127.0.0.1:8000/x", "http://169.254.169.254/", "http://localhost/", "ftp://a.com/"]
)
async def test_relayed_requests_still_block_internal_targets(no_dns, target):
    from qiuyuantu.convert import net

    def handler(request: httpx.Request) -> httpx.Response:
        return _relay_redirect(target)

    client = net.safe_client(transport=_transport(handler), follow_redirects=True)
    # 302 跳到内网也一样拦（ftp 这类协议 httpx 自己就不跟）
    with pytest.raises((net.UnsafeURL, httpx.UnsupportedProtocol)):
        await client.get("https://xhslink.cn/short")
    assert not net.is_safe_url_relayed(target)


async def test_direct_requests_still_resolve(no_dns):
    from qiuyuantu.convert import net

    assert await net.is_safe_url_async("https://www.youtube.com/watch?v=1")
    assert no_dns == ["www.youtube.com"]
