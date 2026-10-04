"""页面渲染：模板、带内容哈希的静态资源地址、内联的首屏 CSS、各页面共用的模板上下文。"""

from __future__ import annotations

import functools
import hashlib
import mimetypes
import re
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .. import guides, seo, status

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


# --------------------------------------------------------------------------- 前端脚本
# static/js/ 下是浏览器原生 ES 模块，模块之间用相对路径 import（'./dom.js'）。相对路径没法各带
# 各的 ?v=，所以整个目录按内容哈希换一个路径前缀：/js/<版本>/app.js 里 import './dom.js'
# 解析出来就是 /js/<版本>/dom.js。改了任何一个文件版本号都会变，浏览器和 nginx 不会拿旧缓存。

JS_DIR = STATIC_DIR / "js"
_JS_IMPORT = re.compile(r"""^import\s[^;]*?\sfrom\s+['"]\./([\w-]+\.js)['"]""", re.M)


@functools.cache
def js_version() -> str:
    digest = hashlib.sha1()
    for path in sorted(JS_DIR.glob("*.js")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:10]


def js_url(name: str) -> str:
    return f"/js/{js_version()}/{name}"


@functools.cache
def js_preloads(entry: str) -> tuple[str, ...]:
    """入口模块直接和间接 import 的所有模块。页面上 <link rel="modulepreload"> 一次列全，
    浏览器并行下载，不用等一层层解析 import（跨境一次往返几百毫秒）。"""
    seen: list[str] = []
    pending = [entry]
    while pending:
        name = pending.pop()
        for dep in _JS_IMPORT.findall((JS_DIR / name).read_text(encoding="utf-8")):
            if dep not in seen and dep != entry:
                seen.append(dep)
                pending.append(dep)
    return tuple(js_url(name) for name in sorted(seen))


templates.env.globals.update(js_url=js_url, js_preloads=js_preloads)


def _secs(ms: float) -> str:
    return f"{ms / 1000:.1f} 秒"


# /status 页用的格式化
templates.env.filters.update(bjtime=status.bj_time, daylabel=status.day_label, secs=_secs, spaced=status.spaced)


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
        "og_image": seo.absolute(base, static_url("og.png")),  # 带版本：分享卡片的抓取方和 nginx 都会长期缓存
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


def render_404(request: Request, status_code: int = 404):
    """status_code=410：以前有、以后也不会再有的页面（旧博客的地址），页面长得一样。"""
    ctx = page_context(request, title="页面不存在 - 求原图", description="这一页不存在。", path=request.url.path)
    return render(request, "404.html", ctx, status_code=status_code)
