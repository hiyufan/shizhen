"""yt-dlp 自带网络栈，httpx 的 SSRF 钩子管不到：在 socket 解析这一层拦（net.public_only）。"""

import http.server
import socket
import threading

import pytest

from parse_video_py.convert import net
from parse_video_py.parser.ytdlp import YtDlp


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


def test_internal_addresses_blocked_only_inside_guard():
    assert socket.getaddrinfo("127.0.0.1", 80)  # 没开防线时照常
    with net.public_only(), pytest.raises(socket.gaierror, match="blocked"):
        socket.getaddrinfo("127.0.0.1", 80)
    with net.public_only(), pytest.raises(socket.gaierror, match="blocked"):
        socket.getaddrinfo("169.254.169.254", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)  # 出了 with 恢复


def test_trusted_services_stay_reachable(monkeypatch):
    # YouTube PO Token 服务（bgutil）就在内网，必须放行
    monkeypatch.setattr(net.config, "POT_URL", "http://localhost:4416")
    with net.public_only():
        assert socket.getaddrinfo("localhost", 4416)


def test_guard_is_per_thread():
    seen = []
    with net.public_only():
        t = threading.Thread(target=lambda: seen.append(bool(socket.getaddrinfo("127.0.0.1", 80))))
        t.start()
        t.join()
    assert seen == [True]  # 别的线程（事件循环、其他请求）不受影响


def test_ytdlp_cannot_reach_internal_address(local_server):
    base, hits = local_server
    with pytest.raises(Exception, match="blocked"):
        YtDlp._extract(base + "/ssrf-probe")
    assert hits == []  # 请求根本没发出去
