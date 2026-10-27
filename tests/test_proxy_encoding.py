"""原始字节代理保留压缩编码，下载客户端才能取得实际的 JSON。"""

import gzip

import httpx
from fastapi.testclient import TestClient

from qiuyuantu import web
from qiuyuantu.convert import net
from qiuyuantu.web import proxy


def test_proxy_retains_content_encoding_for_original_json_download(monkeypatch):
    raw = b'{"v":"5.5.7","w":320,"h":320}'
    compressed = gzip.compress(raw)
    url = "https://cdn.discordapp.com/stickers/796140620111544330.json"

    async def safe(_):
        return True

    async def upstream(request_url, headers):
        assert request_url == url
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
            stream=httpx.ByteStream(compressed),
        )

    monkeypatch.setattr(proxy, "is_safe_url_async", safe)
    monkeypatch.setattr(proxy, "_open_upstream", upstream)
    with TestClient(web.app) as client:
        response = client.get(
            "/api/proxy", params={"url": url, "sig": net.sign(url), "download": 1, "filename": "original.json"}
        )
    assert response.status_code == 200 and response.content == raw
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["content-length"] == str(len(compressed))
    assert "original.json" in response.headers["content-disposition"]
