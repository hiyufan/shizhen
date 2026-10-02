"""内容安全策略：不许跑内联脚本；站长配的统计代码按哈希 / 域名单独放行。"""

import base64
import hashlib

from fastapi.testclient import TestClient

from parse_video_py import web
from parse_video_py.web.middleware import build_csp


def _directives(csp):
    return {d.split()[0]: d.split()[1:] for d in csp.split("; ")}


def test_pages_forbid_inline_scripts():
    csp = TestClient(web.app).get("/").headers["content-security-policy"]
    assert _directives(csp)["script-src"] == ["'self'"]
    assert "'unsafe-inline'" not in _directives(csp)["script-src"]


def test_analytics_snippet_is_allowed_by_hash_and_origin():
    body = "var _hmt = _hmt || [];"
    snippet = f'<script>{body}</script><script async src="https://hm.baidu.com/hm.js?abc"></script>'
    d = _directives(build_csp(snippet, "https://edge.example.com"))
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    assert d["script-src"] == ["'self'", f"'sha256-{digest}'", "https://hm.baidu.com"]
    assert d["img-src"] == ["'self'", "data:", "blob:", "https://edge.example.com", "https://hm.baidu.com"]
    assert d["connect-src"] == ["'self'", "https://hm.baidu.com"]
