"""横幅保留独立沙箱；MultiTag 经站长授权在公开页面执行。"""

import os

from fastapi import APIRouter, HTTPException, Request

from .auth import SITE_AUTH

ENABLED = os.environ.get("PARSE_VIDEO_ADS", "1") == "1"
MULTITAG_SCRIPT_ORIGINS = ("https://quge5.com", "https://auqot.com", "https://ekhay.com", "https://b3mny.com")
# Push 标签会注入 universal.min.js v3.1.647；仅允许该版本内容，不放行任意内联脚本。
MULTITAG_INLINE_HASHES = ("'sha256-qZXLOaBiOd3D4Ypi84lO7EtsJFzqaGjAIHGiXgA/hDM='",)
FORMATS = {
    "desktop": {"key": "b08de1e1c36939e9944cc6ba2c9831be", "width": 728, "height": 90},
    "mobile": {"key": "2ea4abab0d473f37f063621df7d49edc", "width": 320, "height": 50},
}
# 这里只约束隔离的广告文档；主页面继续使用原来的 CSP。
CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self' https://bauval.org",
        "style-src 'self'",
        "img-src https: data:",
        "connect-src https:",
        "frame-src https:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
    )
)
router = APIRouter()


@router.api_route("/api/ads/{placement}", methods=["GET", "HEAD"], include_in_schema=False, dependencies=SITE_AUTH)
async def banner(request: Request, placement: str):
    from .rendering import templates

    if not ENABLED or placement not in FORMATS:
        raise HTTPException(404, "Not Found")
    return templates.TemplateResponse(
        request,
        "ad-frame.html",
        {"ad": FORMATS[placement]},
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": CSP,
            "X-Frame-Options": "SAMEORIGIN",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "origin",
        },
        media_type="text/html",
    )
