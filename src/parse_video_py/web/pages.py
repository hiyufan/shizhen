"""公开页面：首页、教程、SEO 落地页，以及 robots / sitemap / 站长平台的验证文件。

注意 /{slug} 是兜底路由，这个 router 要最后挂到 app 上，不能抢在其它路由前面。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from .. import guides, seo
from .auth import SITE_AUTH
from .rendering import JS_DIR, base_url, js_version, page_context, render, render_404, render_tool_page

router = APIRouter()

_ROBOTS = (
    "User-agent: *\n"
    "Disallow: /api/\n"
    "Disallow: /video/\n"
    "Disallow: /mcp\n"
    "Disallow: /stats\n"
    "Disallow: /*?url=\n"
    "Sitemap: {sitemap}\n"
)
_BAIDU_VERIFY = "codeva-vdJztHZWcG"
_JS_NAME = re.compile(r"^[\w-]+\.js$")


@router.get("/", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def home(request: Request):
    return render_tool_page(request, seo.PAGE_BY_SLUG[""])


@router.get("/guides", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def guides_index(request: Request):
    ctx = page_context(
        request,
        title="教程：抖音小红书图片实况保存、视频转 GIF 和实况照片 - 拾帧",
        description=(
            "拾帧教程：小红书实况图保存到 iPhone、抖音图集原图下载、视频转实况照片、视频转 GIF、"
            "YouTube 1080p 下载，每篇两分钟照着做。"
        ),
        path="/guides",
        keywords="小红书实况图保存,抖音图集下载,视频转实况照片,视频转gif教程",
        json_ld=seo.guides_index_json_ld(base_url(request)),
    )
    ctx.update({"guides": guides.GUIDES, "guides_nav": True})
    return render(request, "guides.html", ctx)


@router.get("/guide/{slug}", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def guide_page(request: Request, slug: str):
    guide = guides.GUIDE_BY_SLUG.get(slug)
    if guide is None:
        return render_404(request)
    tool = seo.PAGE_BY_SLUG.get(guide.tool)
    ctx = page_context(
        request,
        title=f"{guide.title} - 拾帧",
        description=guide.description,
        path=guide.path,
        keywords=guide.keywords,
        json_ld=seo.guide_json_ld(guide, base_url(request)),
        page=tool,
    )
    ctx.update(
        {
            "guide": guide,
            "tool_path": tool.path if tool else "/",
            "related": guides.pick(guide.related),
            "guides_nav": True,
        }
    )
    return render(request, "guide.html", ctx)


@router.get("/sitemap.xml")
async def sitemap(request: Request):
    return Response(seo.sitemap_xml(base_url(request)), media_type="application/xml")


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots(request: Request):
    return _ROBOTS.format(sitemap=seo.absolute(base_url(request), "/sitemap.xml"))


@router.get(f"/{seo.INDEXNOW_KEY}.txt", response_class=PlainTextResponse)
async def indexnow_key():
    # IndexNow 的验证文件：URL 路径含 key，响应体也必须是 key 本身
    return seo.INDEXNOW_KEY


@router.get(f"/baidu_verify_{_BAIDU_VERIFY}.html", response_class=PlainTextResponse)
async def baidu_site_verify():
    # 百度站长平台的文件验证：内容就是验证码字符串，与下载的验证文件一致
    return _BAIDU_VERIFY


@router.get("/js/{version}/{name}")
async def js_module(version: str, name: str):
    """前端模块（见 rendering.js_url）。版本号对得上才长期缓存；对不上的多半是部署前缓存的老页面，
    照样给当前的文件，但别让它被当成那个老版本缓存下来。"""
    path = JS_DIR / name
    if not _JS_NAME.match(name) or not path.is_file():
        raise HTTPException(404, "Not Found")
    current = version == js_version()
    cache = "public, max-age=31536000, immutable" if current else "no-cache"
    return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": cache})


@router.get("/{slug}", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def landing_page(request: Request, slug: str):
    """SEO 落地页：/douyin /xiaohongshu /gif /live-photo ... 同一个工具，不同的标题和文案。"""
    page = seo.PAGE_BY_SLUG.get(slug)
    if page is None or not slug:
        return render_404(request)
    return render_tool_page(request, page)
