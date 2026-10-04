"""最后一道 SSRF 防线：socket.getaddrinfo 换成带检查的（net.install_ssrf_guard）。
yt-dlp 自带网络栈、DNS 重绑定，前面「先解析一次看看」的检查都管不住，只能在真连之前拦。"""

import http.server
import socket
import threading

import pytest

from parse_video_py.convert import net
from parse_video_py.parser.ytdlp import YtDlp


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", net._guarded_getaddrinfo)


@pytest.fixture
def local_server():
    """本机起一个 HTTP 服务，记下有没有被访问到。"""
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "localhost", "::1"])
def test_internal_addresses_are_blocked(guard, host):
    with pytest.raises(socket.gaierror, match="blocked"):
        socket.getaddrinfo(host, 80)


def test_listening_and_trusted_services_still_work(guard, monkeypatch):
    # uvicorn 绑 0.0.0.0 走的是 AI_PASSIVE
    assert socket.getaddrinfo("0.0.0.0", 8000, flags=socket.AI_PASSIVE)
    # YouTube PO Token 服务（bgutil）就在内网，必须放行
    monkeypatch.setattr(net.config, "POT_URL", "http://localhost:4416")
    assert socket.getaddrinfo("localhost", 4416)


def test_ytdlp_cannot_reach_internal_address(guard, local_server):
    base, hits = local_server
    with pytest.raises(Exception, match="blocked"):
        YtDlp._extract(base + "/ssrf-probe")
    assert hits == []  # 请求根本没发出去


async def test_httpx_connect_is_checked_again(guard, local_server):
    # DNS 重绑定：钩子检查时是公网、连接时变成内网。这里跳过钩子直接连一个解析到本机的域名，
    # 连接时的那次解析也得拦住。（字面 IP 不走解析，由 httpx 钩子拦，也不存在重绑定）
    import httpx

    base, hits = local_server
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError, match="blocked"):
            await client.get(base.replace("127.0.0.1", "localhost") + "/rebind")
    assert hits == []
