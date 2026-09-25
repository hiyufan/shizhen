import asyncio
import contextlib
import mimetypes
import dataclasses
import os
import re
import secrets
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

import time

from parse_video_py import VideoSource, parse_video_id, parse_video_share_url
from parse_video_py import stats
from parse_video_py.parser import detect_source
from parse_video_py.convert import config as cconfig
from parse_video_py.convert import ffmpeg, jobs, limits, store, tasks, updater
from parse_video_py.parser.errors import ParseError, classify
from parse_video_py.convert import net, relay
from parse_video_py.convert.net import headers_for, is_safe_url_async, safe_filename
from parse_video_py.utils import extract_url
from parse_video_py import guides as guides_mod
from parse_video_py import seo


def _get_templates_dir() -> str:
    # 模板已移入 src/parse_video_py/templates/，与 web.py 同级
    templates_dir = Path(__file__).parent / "templates"
    if templates_dir.is_dir():
        return str(templates_dir)
    raise FileNotFoundError("templates 目录未找到")


def _cleanup() -> None:
    """过期任务 / 原视频、没登记在册的孤儿文件、磁盘配额，每 5 分钟一轮。"""
    jobs.sweep()
    store.sweep()
    known = jobs.known_paths() | store.known_paths()
    store.sweep_orphans(known)
    store.enforce_quota(known)
    if stats.enabled():
        stats.prune()


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(300)
        with contextlib.suppress(Exception):
            _cleanup()


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    cconfig.ensure_dirs()
    # 任务和原视频的登记在内存里，上次进程留下的文件启动时先清一遍
    with contextlib.suppress(Exception):
        _cleanup()
    tasks_ = [asyncio.create_task(_sweeper())]
    # 中继连接空闲两分钟就凉，重连要 1.3 秒。保活任务进去先戳一下，
    # 顺带把进程启动后的第一条连接也预热了，第一个用户不用替后面的人垫这 1.3 秒
    if relay.enabled():
        tasks_.append(asyncio.create_task(relay.keepalive()))
    if stats.enabled():
        tasks_.append(asyncio.create_task(stats.flusher()))
    if cconfig.YTDLP_AUTOUPDATE_DAYS > 0:
        tasks_.append(asyncio.create_task(updater.loop(cconfig.YTDLP_AUTOUPDATE_DAYS)))
    yield
    for t in tasks_:
        t.cancel()
    # 池化的客户端 aclose() 是空操作, 退出时在这里真关
    with contextlib.suppress(Exception):
        await net.aclose_pool()
        await relay.aclose_shared()


app = FastAPI(lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)

_CSP = (
    "default-src 'self'; img-src 'self' data: blob:" + (" " + relay.edge_origin() if relay.edge_img_enabled() else "")
    + "; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


app.add_middleware(GZipMiddleware, minimum_size=1024)
stats_enabled = stats.enabled()


_BOT_UA = re.compile(r"bot|spider|crawl|slurp|fetch|curl|wget|python|http", re.I)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/"):
        # 静态资源带内容版本号，可以长期缓存
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.method == "GET" and response.headers.get("content-type", "").startswith("text/html") and not path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "public, max-age=600")
        is_bot = _BOT_UA.search(request.headers.get("user-agent", ""))
        if response.status_code == 200 and stats_enabled and path != "/stats" and not is_bot:
            stats.record("view", limits.client_ip(request), source=path)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", _CSP)
    return response

# MCP 是上游的可选功能；公开部署建议 PARSE_VIDEO_MCP=0 关掉，少一个暴露面
mcp = None
if os.environ.get("PARSE_VIDEO_MCP", "1") == "1":
    try:
        from fastapi_mcp import FastApiMCP

        mcp = FastApiMCP(app)
        mcp.mount_http()
    except Exception:  # pragma: no cover - optional dependency
        mcp = None

templates = Jinja2Templates(directory=_get_templates_dir())
mimetypes.add_type("font/woff2", ".woff2")  # Windows 的注册表里没有这个类型
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def _build_auth_dependency() -> list[Depends]:
    """根据环境变量动态构建 Basic Auth 依赖项"""
    basic_auth_username = os.getenv("PARSE_VIDEO_USERNAME")
    basic_auth_password = os.getenv("PARSE_VIDEO_PASSWORD")

    if not (basic_auth_username and basic_auth_password):
        return []

    security = HTTPBasic()

    def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
        correct_username = secrets.compare_digest(
            credentials.username, basic_auth_username
        )
        correct_password = secrets.compare_digest(
            credentials.password, basic_auth_password
        )
        if not (correct_username and correct_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials

    return [Depends(verify_credentials)]


# 模块加载时构建一次，避免每个路由装饰器重复调用
_auth_dependency = _build_auth_dependency()


def _base_url(request: Request) -> str:
    return seo.SITE_URL or str(request.base_url).rstrip("/")


def _asset_version() -> str:
    """静态资源版本号（按内容哈希），改了字体 / 图片浏览器不会用旧缓存。"""
    import hashlib

    static = Path(__file__).parent / "static"
    h = hashlib.sha1()
    for f in sorted(static.glob("*")):
        h.update(f.name.encode())
        h.update(str(f.stat().st_mtime_ns).encode())
    return h.hexdigest()[:8]


_ASSET_V = _asset_version()


def _common_context(request: Request, *, title: str, description: str, path: str, keywords: str = "",
                    json_ld: str = "") -> dict:
    base = _base_url(request)
    return {
        "title": title,
        "description": description,
        "keywords": keywords,
        "pages": seo.PAGES,
        "canonical": seo.absolute(base, path),
        "og_image": seo.absolute(base, "/static/og.png"),
        "json_ld": json_ld,
        "site_name": seo.SITE_NAME,
        "site_verification": seo.SITE_VERIFICATION_HTML,
        "analytics": seo.ANALYTICS_HTML,
        "v": _ASSET_V,
    }


def _render_page(request: Request, page: seo.Page):
    base = _base_url(request)
    ctx = _common_context(request, title=page.title, description=page.description, path=page.path,
                          keywords=page.keywords, json_ld=seo.json_ld(page, base))
    ctx.update({
        "page": page,
        "faq": page.all_faq,
        "related_guides": [guides_mod.GUIDE_BY_SLUG[g] for g in page.guides if g in guides_mod.GUIDE_BY_SLUG],
    })
    return templates.TemplateResponse(request=request, name="index.html", context=ctx)


def _render_404(request: Request):
    ctx = _common_context(request, title="页面不存在 - 拾帧", description="这一页不存在。", path=request.url.path)
    ctx["page"] = seo.PAGE_BY_SLUG[""]
    return templates.TemplateResponse(request=request, name="404.html", context=ctx, status_code=404)


@app.get("/guides", response_class=HTMLResponse, dependencies=_auth_dependency)
async def guides_index(request: Request):
    base = _base_url(request)
    ctx = _common_context(
        request, title="教程：抖音小红书图片实况保存、视频转 GIF 和实况照片 - 拾帧",
        description="拾帧教程：小红书实况图保存到 iPhone、抖音图集原图下载、视频转实况照片、视频转 GIF、YouTube 1080p 下载，每篇两分钟照着做。",
        path="/guides", keywords="小红书实况图保存,抖音图集下载,视频转实况照片,视频转gif教程",
        json_ld=seo._dump([seo._crumbs(base, (seo.SITE_NAME, "/"), ("教程", "/guides"))]),
    )
    ctx.update({"page": seo.PAGE_BY_SLUG[""], "guides": guides_mod.GUIDES, "guides_nav": True})
    return templates.TemplateResponse(request=request, name="guides.html", context=ctx)


@app.get("/guide/{slug}", response_class=HTMLResponse, dependencies=_auth_dependency)
async def guide_page(request: Request, slug: str):
    guide = guides_mod.GUIDE_BY_SLUG.get(slug)
    if guide is None:
        return _render_404(request)
    base = _base_url(request)
    ctx = _common_context(request, title=guide.title + " - 拾帧", description=guide.description, path=guide.path,
                          keywords=guide.keywords, json_ld=seo.guide_json_ld(guide, base))
    ctx.update({
        "page": seo.PAGE_BY_SLUG.get(guide.tool) or seo.PAGE_BY_SLUG[""],
        "guide": guide,
        "tool_path": seo.PAGE_BY_SLUG[guide.tool].path if guide.tool in seo.PAGE_BY_SLUG else "/",
        "related": [guides_mod.GUIDE_BY_SLUG[r] for r in guide.related if r in guides_mod.GUIDE_BY_SLUG],
        "guides_nav": True,
    })
    return templates.TemplateResponse(request=request, name="guide.html", context=ctx)


@app.get("/", response_class=HTMLResponse, dependencies=_auth_dependency)
async def read_item(request: Request):
    return _render_page(request, seo.PAGE_BY_SLUG[""])


@app.get("/sitemap.xml")
async def sitemap(request: Request):
    return Response(seo.sitemap_xml(_base_url(request)), media_type="application/xml")


# --------------------------------------------------------------------------- 上游接口


@app.get("/video/share/url/parse", dependencies=_auth_dependency)
async def share_url_parse(url: str):
    video_share_url = extract_url(url)
    if video_share_url is None or not await is_safe_url_async(video_share_url):
        return {
            "code": 400,
            "msg": "未检测到有效的分享链接",
        }

    try:
        video_info = await parse_video_share_url(video_share_url)
        return {
            "code": 200,
            "msg": "解析成功",
            "data": dataclasses.asdict(video_info),
        }
    except Exception as err:
        return {
            "code": 500,
            "msg": str(err),
        }


@app.get("/video/id/parse", dependencies=_auth_dependency)
async def video_id_parse(source: VideoSource, video_id: str):
    try:
        video_info = await parse_video_id(source, video_id)
        return {
            "code": 200,
            "msg": "解析成功",
            "data": dataclasses.asdict(video_info),
        }
    except Exception as err:
        return {
            "code": 500,
            "msg": str(err),
        }


# --------------------------------------------------------------------------- 本项目新增接口


class PrepareRequest(BaseModel):
    url: str = ""                      # 直链
    page_url: str = ""                 # yt-dlp 站点的页面地址
    format_spec: str = ""              # yt-dlp -f 表达式（可选）
    headers: dict[str, str] = Field(default_factory=dict)
    title: str = ""
    sig: str = ""                      # 解析结果里给的签名，证明这个地址是我们解析出来的


class ConvertRequest(BaseModel):
    source_id: str
    format: str = Field(pattern="^(gif|livephoto|motionphoto)$")
    start: float = 0.0
    end: Optional[float] = None
    fps: int = 12
    width: int = 480
    dither: str = Field(default="bayer", pattern="^(bayer|sierra2_4a|none)$")
    speed: float = 1.0
    key_time: Optional[float] = None


class DownloadRequest(BaseModel):
    page_url: str
    format_spec: str
    title: str = ""
    ext: str = "mp4"
    sig: str = ""


class LiveItem(BaseModel):
    image_url: str
    video_url: str
    image_sig: str = ""
    video_sig: str = ""


class LiveRequest(BaseModel):
    items: list[LiveItem] = Field(min_length=1, max_length=30)
    format: str = Field(default="livephoto", pattern="^(livephoto|motionphoto)$")
    title: str = ""


def _start_job(job_type: str, fn, ip: str, source_id: str | None = None) -> jobs.Job:
    """入队并记到该 IP 的配额上；队列满 / 配额满都返回 429 或 503。"""
    limits.jobs_per_ip.acquire(ip)
    try:
        return jobs.start(job_type, fn, source_id, owner=ip,
                          on_release=lambda: limits.jobs_per_ip.release(ip))
    except jobs.QueueFull as err:
        limits.jobs_per_ip.release(ip)
        raise HTTPException(503, str(err), headers={"Retry-After": "30"})
    except Exception:
        limits.jobs_per_ip.release(ip)
        raise


def _job_or_404(job_id: str) -> jobs.Job:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或已过期")
    return job


def _source_or_404(source_id: str) -> store.Source:
    src = store.get(source_id)
    if not src:
        raise HTTPException(404, "原视频已过期，请重新解析")
    return src


@app.get("/api/health")
async def api_health():
    """给负载均衡 / 监控用。"""
    return {
        "ok": True, "jobs": jobs.stats(), "disk_used": store.disk_usage(), "disk_quota": cconfig.DISK_QUOTA_BYTES,
        "ytdlp": updater.current_version(), "pot": bool(cconfig.POT_URL), "cache": len(_parse_cache),
    }


# 解析结果缓存: 爆款链接短时间被很多人贴, 不必每次都去打平台
_parse_cache: dict[str, tuple[float, dict]] = {}


def _cache_get(key: str):
    hit = _parse_cache.get(key)
    if not hit:
        return None
    ts, value = hit
    ttl = cconfig.PARSE_CACHE_SECONDS if value.get("code") == 200 else 60
    if time.time() - ts > ttl:
        _parse_cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: dict) -> None:
    if cconfig.PARSE_CACHE_SECONDS <= 0:
        return
    if len(_parse_cache) >= cconfig.PARSE_CACHE_SIZE:
        oldest = min(_parse_cache.items(), key=lambda kv: kv[1][0])[0]
        _parse_cache.pop(oldest, None)
    _parse_cache[key] = (time.time(), value)


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots(request: Request):
    return (
        "User-agent: *\nDisallow: /api/\nDisallow: /video/\nDisallow: /mcp\nDisallow: /stats\nDisallow: /*?url=\n"
        f"Sitemap: {seo.absolute(_base_url(request), '/sitemap.xml')}\n"
    )


@app.get("/api/parse", dependencies=_auth_dependency)
async def api_parse(url: str, _ip: str = Depends(limits.parse_limit)):
    """解析分享文本 / 链接，返回可下载的视频、图片和清晰度选项。"""
    share_url = extract_url(url)
    if share_url is None:
        stats.record("parse", _ip, ok=False, reason="unsupported")
        return {"code": 400, "msg": "没有找到链接，请粘贴完整的分享内容", "reason": "unsupported"}
    platform = detect_source(share_url).value
    if cached := _cache_get(share_url):
        stats.record("parse", _ip, source=(cached.get("data") or {}).get("source") or platform,
                     ok=cached.get("code") == 200, reason="cache")
        return cached
    if not await is_safe_url_async(share_url):
        stats.record("parse", _ip, source=platform, ok=False, reason="unsupported")
        return {"code": 400, "msg": "不支持这个地址", "reason": "unsupported"}
    t0 = time.monotonic()
    try:
        info = await asyncio.wait_for(parse_video_share_url(share_url), 90)
    except asyncio.TimeoutError:
        result = {"code": 504, "msg": "解析超时，请稍后再试", "reason": "timeout"}
    except ParseError as err:
        result = {"code": 500, "msg": str(err), "reason": err.reason}
    except Exception as err:  # noqa: BLE001
        perr = classify(err)
        result = {"code": 500, "msg": str(perr), "reason": perr.reason}
    else:
        data = dataclasses.asdict(info)
        data["share_url"] = share_url
        urls = {data.get("video_url"), data.get("cover_url"), data.get("music_url"), data.get("page_url"), share_url}
        urls |= {i.get("url") for i in data["images"]} | {i.get("live_photo_url") for i in data["images"]}
        urls |= {f.get("url") for f in data["formats"]}
        data["sig"] = {u: net.sign(u) for u in urls if u}
        if relay.edge_img_enabled():
            # 结果会被缓存 PARSE_CACHE_SECONDS, 边缘签名要比缓存活得久
            ttl = cconfig.PARSE_CACHE_SECONDS + 3600
            imgs = {data.get("cover_url")} | {i.get("url") for i in data["images"]}
            data["edge"] = {u: e for u in imgs if u and (e := relay.edge_img_url(u, ttl))}
        result = {"code": 200, "msg": "解析成功", "data": data}
    stats.record("parse", _ip, source=(result.get("data") or {}).get("source") or platform,
                 ok=result["code"] == 200, reason=result.get("reason", ""), ms=(time.monotonic() - t0) * 1000)
    _cache_put(share_url, result)
    return result


@app.get("/api/proxy", dependencies=_auth_dependency)
async def api_proxy(request: Request, url: str, filename: str = "", download: int = 0, sig: str = "",
                    ip: str = Depends(limits.proxy_limit)):
    """把第三方直链转发给浏览器：补 Referer/UA，透传 Range，可选加下载头。

    只转发带有效签名的地址（即 /api/parse 返回过的），不做开放代理。
    """
    if not net.verify(url, sig):
        raise HTTPException(403, "这个地址不是解析结果里的，拒绝转发")
    if not await is_safe_url_async(url):
        raise HTTPException(400, "不支持的地址")
    upstream_headers = headers_for(url)
    if rng := request.headers.get("range"):
        upstream_headers["Range"] = rng

    # 并发额度是留给视频流的(长连接、一直占带宽), 图片不进这个池子
    held = not limits.looks_like_image(url)
    if held:
        limits.proxy_streams.acquire(ip)

    def release() -> None:
        nonlocal held
        if held:
            held = False
            limits.proxy_streams.release(ip)

    # 池化的客户端, 连接归池子管, 这里只负责关掉响应。播放器拖进度条会打一连串
    # Range 请求, 每个都重新握手的话实测要 1457ms/次, 复用后省掉握手和 TCP 慢启动
    client = net.safe_client(for_url=url, follow_redirects=True, timeout=httpx.Timeout(30, read=120))
    req = client.build_request("GET", url, headers=upstream_headers)
    try:
        resp = await client.send(req, stream=True)
    except net.UnsafeURL:
        release()
        raise HTTPException(400, "源站跳转到了不允许的地址")
    except httpx.HTTPError as err:
        release()
        raise HTTPException(502, f"拉取失败: {err.__class__.__name__}")
    if resp.status_code >= 400:
        await resp.aclose()
        release()
        raise HTTPException(resp.status_code, "源站拒绝了请求")

    # URL 没认出来但响应头说是图片, 那也早点把槽还回去
    if resp.headers.get("content-type", "").startswith("image/"):
        release()

    passthrough = {}
    for key in ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag"):
        if key in resp.headers:
            passthrough[key] = resp.headers[key]
    passthrough.setdefault("accept-ranges", "bytes")
    passthrough["cache-control"] = "private, max-age=3600"
    if download:
        ctype = passthrough.get("content-type", "")
        ext = "mp4" if "video" in ctype else "jpg" if "jpeg" in ctype else "png" if "png" in ctype else "webp" if "webp" in ctype else ""
        name = filename or safe_filename("media", ext)
        if ext and not name.lower().endswith("." + ext) and "." not in name[-5:]:
            name += "." + ext
        from urllib.parse import quote

        passthrough["content-disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
        host = (httpx.URL(url).host or "").rsplit(".", 2)
        stats.record("download", ip, source=".".join(host[-2:]))

    async def body():
        try:
            async for chunk in resp.aiter_raw(1 << 16):
                yield chunk
        finally:
            await resp.aclose()
            release()

    return StreamingResponse(body(), status_code=resp.status_code, headers=passthrough)


@app.post("/api/prepare", dependencies=_auth_dependency)
async def api_prepare(req: PrepareRequest, ip: str = Depends(limits.job_limit)):
    """把原视频缓存到服务端，供转换预览 / 裁剪使用。已缓存则立即返回。"""
    target = req.page_url or req.url
    if not target:
        raise HTTPException(400, "缺少视频地址")
    if not net.verify(target, req.sig):
        raise HTTPException(403, "这个地址不是解析结果里的")
    if not await is_safe_url_async(target):
        raise HTTPException(400, "不支持的地址")
    if req.page_url:
        sid = store.source_id_for(req.page_url, req.format_spec or "default")
    else:
        sid = store.source_id_for(req.url)

    if src := store.get(sid):
        return {"ready": True, "source": src.view()}
    # 同一个来源已经有人在准备了（爆款链接常见），跟着等那个任务就行，别再下一份
    if pending := jobs.find_pending("prepare", sid):
        return {"ready": False, "job": pending.view(), "source_id": sid}

    async def fn(job: jobs.Job) -> None:
        await tasks.fetch_source(job, source_id=sid, url=req.url, headers=req.headers,
                                 page_url=req.page_url, format_spec=req.format_spec, title=req.title)

    job = _start_job("prepare", fn, ip, sid)
    return {"ready": False, "job": job.view(), "source_id": sid}


@app.post("/api/upload", dependencies=_auth_dependency)
async def api_upload(file: UploadFile = File(...), _ip: str = Depends(limits.upload_limit)):
    """本地视频也能转 GIF / 实况。"""
    cconfig.ensure_dirs()
    sid = uuid.uuid4().hex[:16]
    suffix = Path(file.filename or "").suffix.lower() or ".mp4"
    dest = cconfig.UPLOADS_DIR / f"{sid}{suffix}"
    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > cconfig.MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "文件太大")
            f.write(chunk)
    info = await ffmpeg.probe(dest)
    if info.duration <= 0 or info.width <= 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "这个文件不是可识别的视频")
    src = store.put(store.Source(
        id=sid, path=str(dest), title=Path(file.filename or "video").stem,
        duration=info.duration, width=info.width, height=info.height, fps=info.fps,
    ))
    return {"ready": True, "source": src.view()}


@app.get("/api/source/{source_id}", dependencies=_auth_dependency)
async def api_source_file(source_id: str):
    src = _source_or_404(source_id)
    return FileResponse(src.path, media_type="video/mp4")


@app.get("/api/source/{source_id}/strip", dependencies=_auth_dependency)
async def api_source_strip(source_id: str, n: int = 16):
    src = _source_or_404(source_id)
    try:
        path = await tasks.make_strip(src, frames=n)
    except Exception as err:
        raise HTTPException(500, str(err))
    return FileResponse(path, media_type="image/jpeg", headers={"cache-control": "private, max-age=3600"})


@app.post("/api/convert", dependencies=_auth_dependency)
async def api_convert(req: ConvertRequest, ip: str = Depends(limits.job_limit)):
    src = _source_or_404(req.source_id)

    async def fn(job: jobs.Job) -> None:
        await tasks.convert(job, src=src, fmt=req.format, start=req.start, end=req.end, fps=req.fps,
                            width=req.width, dither=req.dither, speed=req.speed, key_time=req.key_time)

    job = _start_job(req.format, fn, ip, src.id)
    return job.view()


@app.post("/api/download", dependencies=_auth_dependency)
async def api_download(req: DownloadRequest, ip: str = Depends(limits.job_limit)):
    """需要服务端合并的清晰度：先下载再给文件。"""
    if not net.verify(req.page_url, req.sig):
        raise HTTPException(403, "这个地址不是解析结果里的")
    if not await is_safe_url_async(req.page_url):
        raise HTTPException(400, "无效的页面地址")

    async def fn(job: jobs.Job) -> None:
        await tasks.download_for_user(job, page_url=req.page_url, format_spec=req.format_spec,
                                      title=req.title, ext=req.ext)

    job = _start_job("download", fn, ip)
    return job.view()


@app.post("/api/live", dependencies=_auth_dependency)
async def api_live(req: LiveRequest, ip: str = Depends(limits.job_limit)):
    """平台自带的实况图 (原图 + 短视频) 直接打包, 不经过裁剪。"""
    for it in req.items:
        if not (net.verify(it.image_url, it.image_sig) and net.verify(it.video_url, it.video_sig)):
            raise HTTPException(403, "这个地址不是解析结果里的")
        if not (await is_safe_url_async(it.image_url) and await is_safe_url_async(it.video_url)):
            raise HTTPException(400, "不支持的地址")

    async def fn(job: jobs.Job) -> None:
        await tasks.pair_live(job, items=[it.model_dump() for it in req.items], fmt=req.format, title=req.title)

    job = _start_job("live", fn, ip)
    return job.view()


@app.get("/api/jobs/{job_id}", dependencies=_auth_dependency)
async def api_job(job_id: str):
    return _job_or_404(job_id).view()


@app.delete("/api/jobs/{job_id}", dependencies=_auth_dependency)
async def api_job_cancel(job_id: str, request: Request):
    job = _job_or_404(job_id)
    if job.owner and job.owner != limits.client_ip(request):
        raise HTTPException(403, "只能取消自己的任务")
    return {"cancelled": jobs.cancel(job_id)}


@app.get("/api/jobs/{job_id}/file", dependencies=_auth_dependency)
async def api_job_file(job_id: str, inline: int = 0):
    job = _job_or_404(job_id)
    if job.status != "done" or not job.result_path or not Path(job.result_path).exists():
        raise HTTPException(404, "结果还没准备好")
    name = job.filename or Path(job.result_path).name
    media_type = {
        ".gif": "image/gif", ".jpg": "image/jpeg", ".zip": "application/zip",
        ".mp4": "video/mp4", ".m4a": "audio/mp4", ".webm": "video/webm",
    }.get(Path(job.result_path).suffix.lower(), "application/octet-stream")
    if inline:
        return FileResponse(job.result_path, media_type=media_type)
    return FileResponse(job.result_path, media_type=media_type, filename=name)


@app.get("/api/jobs/{job_id}/preview", dependencies=_auth_dependency)
async def api_job_preview(job_id: str):
    """实况照片的封面图（zip 里的 JPG）。"""
    job = _job_or_404(job_id)
    path = (job.extra or {}).get("preview_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "没有预览")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/jobs/{job_id}/video", dependencies=_auth_dependency)
async def api_job_video(job_id: str):
    """实况照片的 MOV 部分，用于页面上按住预览。"""
    job = _job_or_404(job_id)
    path = (job.extra or {}).get("mov_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "没有视频")
    return FileResponse(path, media_type="video/quicktime")


# --------------------------------------------------------------------------- 使用统计（站长）


_STATS_RANGES = {"24h": (86400, 3600), "7d": (7 * 86400, 86400), "30d": (30 * 86400, 86400), "90d": (90 * 86400, 86400)}


def _stats_auth(request: Request, token: str = "") -> None:
    auth = request.headers.get("authorization", "")
    bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not stats.check_token(token or bearer):
        # 没配 token 时假装这个页面不存在
        raise HTTPException(404, "Not Found")


@app.get("/api/stats")
async def api_stats(request: Request, range: str = "24h", token: str = "", tz: int = 0):
    """按时间分桶的使用量：浏览 / 解析 / 任务 / 下载 / 人数，以及各平台成功率。"""
    _stats_auth(request, token)
    span, step = _STATS_RANGES.get(range, _STATS_RANGES["24h"])
    tz_offset = max(-14 * 3600, min(14 * 3600, -tz * 60))   # JS 的 getTimezoneOffset 是"UTC 减本地"的分钟数
    now = time.time()
    since = now - span
    since -= (since + tz_offset) % step   # 对齐到桶的起点，最左一格才是完整的
    await asyncio.to_thread(stats.flush)
    data = await asyncio.to_thread(stats.summary, since, now, step, tz_offset)
    data["range"] = range
    return data


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, token: str = ""):
    _stats_auth(request, token)
    ctx = _common_context(request, title="使用统计 - 拾帧", description="", path="/stats")
    ctx.update({"page": seo.PAGE_BY_SLUG[""], "ranges": list(_STATS_RANGES), "token": token})
    return templates.TemplateResponse(request=request, name="stats.html", context=ctx,
                                      headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


# 放在最后：/{slug} 是兜底路由，不能抢在 /robots.txt /sitemap.xml 前面
@app.get("/{slug}", response_class=HTMLResponse, dependencies=_auth_dependency)
async def landing_page(request: Request, slug: str):
    """SEO 落地页：/douyin /xiaohongshu /gif /live-photo ... 同一个工具，不同的标题和文案。"""
    page = seo.PAGE_BY_SLUG.get(slug)
    if page is None or not slug:
        return _render_404(request)
    return _render_page(request, page)


if mcp is not None:
    mcp.setup_server()
