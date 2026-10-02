"""页面渲染：模板、带内容哈希的静态资源地址、内联的首屏 CSS、各页面共用的模板上下文。"""

from __future__ import annotations

import functools
import hashlib
import mimetypes
import re
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .. import guides, seo

PACKAGE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATES_DIR = PACKAGE_DIR / "templates"

# 站长页（/stats、/test）不缓存、不进搜索引擎
PRIVATE_PAGE_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}

mimetypes.add_type("font/woff2", ".woff2")  # Windows 的注册表里没有这个类型
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@functools.cache
def static_url(name: str) -> str:
    """/static/<name>?v=<这个文件的内容哈希>。/static/ 在 nginx 缓存 30 天、浏览器按 immutable
    缓存一年，换了内容（比如重裁字体子集）URL 不变的话，老访客和 nginx 会一直发旧文件。
    按单个文件算，改 site.css 不会让所有字体跟着失效重下。"""
    digest = hashlib.sha1((STATIC_DIR / name).read_bytes()).hexdigest()[:8]
    return f"/static/{name}?v={digest}"


templates.env.globals["static_url"] = static_url


def _critical_css() -> str:
    """fonts.css + site.css 原文，内联进 <head>：省掉首屏两个渲染阻塞请求。
    跨境访问一次 RTT 就是几百毫秒，且 @font-face 随 HTML 到达后 woff2 才能开始
    并行下载——之前字体被串在 fonts.css 的发现链上。文件在部署时随镜像更新，
    进程启动读一次即可。字体 URL 换成带版本的，和 base.html 的 preload 一致。"""
    css = "".join((STATIC_DIR / name).read_text(encoding="utf-8") for name in ("fonts.css", "site.css"))
    return re.sub(r"url\(/static/([^)?]+)\)", lambda m: f"url({static_url(m.group(1))})", css)


_CRITICAL_CSS = _critical_css()


def base_url(request: Request) -> str:
    return seo.SITE_URL or str(request.base_url).rstrip("/")


def page_context(
    request: Request,
    *,
    title: str,
    description: str = "",
    path: str,
    keywords: str = "",
    json_ld: str = "",
    page: seo.Page | None = None,
) -> dict:
    """base.html 需要的上下文；page 决定顶部导航高亮哪一项，不给就是首页。"""
    base = base_url(request)
    return {
        "title": title,
        "description": description,
        "keywords": keywords,
        "pages": seo.PAGES,
        "page": page or seo.PAGE_BY_SLUG[""],
        "canonical": seo.absolute(base, path),
        "og_image": seo.absolute(base, "/static/og.png"),
        "json_ld": json_ld,
        "site_name": seo.SITE_NAME,
        "site_verification": seo.SITE_VERIFICATION_HTML,
        "analytics": seo.ANALYTICS_HTML,
        "critical_css": _CRITICAL_CSS,
    }


def render(request: Request, name: str, context: dict, **kwargs):
    return templates.TemplateResponse(request=request, name=name, context=context, **kwargs)


def render_tool_page(request: Request, page: seo.Page):
    """首页和各个 SEO 落地页：同一个工具，不同的标题和文案。"""
    ctx = page_context(
        request,
        title=page.title,
        description=page.description,
        path=page.path,
        keywords=page.keywords,
        json_ld=seo.json_ld(page, base_url(request)),
        page=page,
    )
    ctx["faq"] = page.all_faq
    ctx["related_guides"] = guides.pick(page.guides)
    return render(request, "index.html", ctx)


def render_404(request: Request):
    ctx = page_context(request, title="页面不存在 - 拾帧", description="这一页不存在。", path=request.url.path)
    return render(request, "404.html", ctx, status_code=404)
