"""公开页面：首页、教程、SEO 落地页，以及 robots / sitemap / 站长平台的验证文件。

注意 /{slug} 是兜底路由，这个 router 要最后挂到 app 上，不能抢在其它路由前面。
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from .. import guides, seo, status
from .auth import SITE_AUTH
from .rendering import JS_DIR, STATIC_DIR, base_url, js_version, page_context, render, render_404, render_tool_page

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
# 搜索结果里的站点图标从根路径读（百度只认 /favicon.ico），文件由 scripts/make_icons.py 生成
_ROOT_ICONS = {"favicon.ico": "image/x-icon", "favicon.svg": "image/svg+xml", "apple-touch-icon.png": "image/png"}
# 这个域名以前是 Halo 博客，搜索引擎还记着它的地址（/about、/tags/...、/upload/...），隔几天回来抓一次。
# 回 410 让它们尽快删掉，404 会被当成「可能暂时没了」反复重试。以后要用这些路径做新页面，先从这里删掉
_OLD_BLOG = (
    "about", "nav", "wishes", "photos", "moments", "footprints", "archives",
    "categories", "tags", "links", "upload", "themes", "plugins", "rss.xml",
)  # fmt: skip


def page_route(path: str, **kwargs):
    """页面类的路由同时接 HEAD：有的爬虫、链接检查先发 HEAD 探一下，405 会被当成页面坏了。
    响应体 uvicorn 遇到 HEAD 自己会丢掉。页面不进 OpenAPI（MCP 照着它生成工具，页面不该是工具）。"""
    return router.api_route(path, methods=["GET", "HEAD"], include_in_schema=False, **kwargs)


@page_route("/", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def home(request: Request):
    return render_tool_page(request, seo.PAGE_BY_SLUG[""])


@page_route("/guides", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def guides_index(request: Request):
    ctx = page_context(
        request,
        title="教程：抖音小红书图片实况保存、视频转 GIF 和实况照片 - 求原图",
        description=(
            "求原图教程：小红书实况图保存到 iPhone、抖音图集原图下载、视频转实况照片、视频转 GIF、"
            "YouTube 1080p 下载，每篇两分钟照着做。"
        ),
        path="/guides",
        keywords="小红书实况图保存,抖音图集下载,视频转实况照片,视频转gif教程",
        json_ld=seo.guides_index_json_ld(base_url(request)),
    )
    ctx.update({"guides": guides.GUIDES, "guides_nav": True})
    return render(request, "guides.html", ctx)


@page_route("/guide/{slug}", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def guide_page(request: Request, slug: str):
    guide = guides.GUIDE_BY_SLUG.get(slug)
    if guide is None:
        return render_404(request)
    tool = seo.PAGE_BY_SLUG.get(guide.tool)
    ctx = page_context(
        request,
        title=f"{guide.title} - 求原图",
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


@page_route("/status", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def status_page(request: Request):
    snap = await asyncio.to_thread(status.snapshot)
    headline = status.headline(snap)
    ctx = page_context(
        request,
        title="各平台解析状态：抖音、小红书、快手现在能不能解析 - 求原图",
        description=headline + "每小时自检抖音、小红书、快手、B站、YouTube，附近 7 天真实用户的解析成功率。",
        path="/status",
        keywords="抖音解析不了,抖音去水印失效,小红书解析失败,快手解析失败,去水印网站能用吗",
        json_ld=seo.status_json_ld(base_url(request), status.FAQ),
    )
    ctx.update(
        {"platforms": snap["platforms"], "headline": headline, "faq": status.FAQ, "reason_labels": status.REASON_LABELS}
    )
    # 状态每小时变，别让浏览器和 nginx 拿着旧的看半天
    return render(request, "status.html", ctx, headers={"Cache-Control": "public, max-age=300"})


@page_route("/sitemap.xml")
async def sitemap(request: Request):
    return Response(seo.sitemap_xml(base_url(request)), media_type="application/xml")


@page_route("/robots.txt", response_class=PlainTextResponse)
async def robots(request: Request):
    return _ROBOTS.format(sitemap=seo.absolute(base_url(request), "/sitemap.xml"))


@page_route(f"/{seo.INDEXNOW_KEY}.txt", response_class=PlainTextResponse)
async def indexnow_key():
    # IndexNow 的验证文件：URL 路径含 key，响应体也必须是 key 本身
    return seo.INDEXNOW_KEY


@page_route(f"/baidu_verify_{_BAIDU_VERIFY}.html", response_class=PlainTextResponse)
async def baidu_site_verify():
    # 百度站长平台的文件验证：内容就是验证码字符串，与下载的验证文件一致
    return _BAIDU_VERIFY


@page_route("/js/{version}/{name}")
async def js_module(version: str, name: str):
    """前端模块（见 rendering.js_url）。版本号对得上才长期缓存；对不上的多半是部署前缓存的老页面，
    照样给当前的文件，但别让它被当成那个老版本缓存下来。"""
    path = JS_DIR / name
    if not _JS_NAME.match(name) or not path.is_file():
        raise HTTPException(404, "Not Found")
    current = version == js_version()
    cache = "public, max-age=31536000, immutable" if current else "no-cache"
    return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": cache})


def _root_icon(name: str, media_type: str):
    async def icon():
        return FileResponse(
            STATIC_DIR / name, media_type=media_type, headers={"Cache-Control": "public, max-age=604800"}
        )

    return icon


async def _old_blog_page(request: Request):
    return render_404(request, status_code=410)


for _name, _type in _ROOT_ICONS.items():
    page_route(f"/{_name}")(_root_icon(_name, _type))
for _prefix in _OLD_BLOG:
    page_route(f"/{_prefix}")(_old_blog_page)
    page_route(f"/{_prefix}/{{rest:path}}")(_old_blog_page)


# 兜底路由，必须在最后
@page_route("/{slug}", response_class=HTMLResponse, dependencies=SITE_AUTH)
async def landing_page(request: Request, slug: str):
    """SEO 落地页：/douyin /xiaohongshu /gif /live-photo ... 同一个工具，不同的标题和文案。"""
    page = seo.PAGE_BY_SLUG.get(slug)
    if page is None or not slug:
        return render_404(request)
    return render_tool_page(request, page)
