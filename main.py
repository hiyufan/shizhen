import os

import uvicorn

from qiuyuantu.web import app

if __name__ == "__main__":
    # 放在 Nginx / Caddy 后面时设 PARSE_VIDEO_TRUST_PROXY=1，限流才能拿到真实客户端 IP
    trust_proxy = os.environ.get("PARSE_VIDEO_TRUST_PROXY", "0") == "1"
    uvicorn.run(
        app,
        host=os.environ.get("PARSE_VIDEO_HOST", "0.0.0.0"),
        port=int(os.environ.get("PARSE_VIDEO_PORT", "8000")),
        proxy_headers=trust_proxy,
        forwarded_allow_ips="*" if trust_proxy else None,
        timeout_keep_alive=15,
        # 事件循环必须是 asyncio，不能用 uvloop：uvloop 用 libuv 自己解析域名、不经过 socket.getaddrinfo，
        # net.install_ssrf_guard 这道最后的 SSRF 防线会被整个绕过（2026-10 实测 localhost 照样解析成 127.0.0.1）。
        # 环境里只要装了 uvloop（比如别的包带进来的 uvicorn[standard]），默认的 "auto" 就会选它，所以这里写死
        loop="asyncio",
        # HTTP 解析用 httptools（C 实现，比纯 Python 的 h11 快）；没装就退回 h11
        http="auto",
    )
