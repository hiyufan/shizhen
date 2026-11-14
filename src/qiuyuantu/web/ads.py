"""固定横幅的隔离文档；广告脚本不在工具页面的执行环境中运行。"""

from fastapi import APIRouter, HTTPException, Request

from .rendering import templates

router = APIRouter()

BANNERS = {
    "mobile": {"key": "2ea4abab0d473f37f063621df7d49edc", "width": 320, "height": 50},
    "desktop": {"key": "b08de1e1c36939e9944cc6ba2c9831be", "width": 728, "height": 90},
}

# 不给广告同源权限、顶层跳转、表单、下载和 Worker 权限。
# 允许新窗口用于用户点击广告后的落地页；不接入 Popunder / Social Bar / 推送脚本。
SANDBOX = "allow-scripts allow-popups allow-popups-to-escape-sandbox"
BANNER_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self' https://bauval.org",
        "style-src 'self' 'unsafe-inline'",
        "img-src https: data:",
        "connect-src https:",
        "frame-src https:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
        f"sandbox {SANDBOX}",
    )
)


@router.api_route("/ads/banner/{placement}", methods=["GET", "HEAD"], include_in_schema=False)
async def banner_frame(request: Request, placement: str):
    banner = BANNERS.get(placement)
    if banner is None:
        raise HTTPException(404, "Not Found")
    return templates.TemplateResponse(
        request=request,
        name="ad-frame.html",
        context={"banner": banner},
        headers={
            "Content-Security-Policy": BANNER_CSP,
            "X-Frame-Options": "SAMEORIGIN",
            # 最多披露站点来源；sandbox 或浏览器也可能直接省略 Referer。
            "Referrer-Policy": "origin",
            "X-Robots-Tag": "noindex, nofollow",
            "Cache-Control": "no-store",
        },
    )
