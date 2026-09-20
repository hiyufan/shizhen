import os

import uvicorn

from parse_video_py.web import app

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
    )
