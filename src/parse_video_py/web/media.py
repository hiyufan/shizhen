"""转换与下载：准备原视频、上传本地视频、转 GIF / 实况、服务端合并下载，以及这些后台任务的查询。"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..convert import config, ffmpeg, jobs, net, store, tasks
from ..convert.net import is_safe_url_async
from . import limits
from .auth import SITE_AUTH

router = APIRouter(dependencies=SITE_AUTH)

_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".zip": "application/zip",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".webm": "video/webm",
}


class PrepareRequest(BaseModel):
    url: str = ""  # 直链
    page_url: str = ""  # yt-dlp 站点的页面地址
    format_spec: str = ""  # yt-dlp -f 表达式（可选）
    headers: dict[str, str] = Field(default_factory=dict)
    title: str = ""
    sig: str = ""  # 解析结果里给的签名，证明这个地址是我们解析出来的


class ConvertRequest(BaseModel):
    source_id: str
    format: str = Field(pattern="^(gif|livephoto|motionphoto)$")
    start: float = 0.0
    end: float | None = None
    fps: int = 12
    width: int = 480
    dither: str = Field(default="bayer", pattern="^(bayer|sierra2_4a|none)$")
    speed: float = 1.0
    key_time: float | None = None
    # GIF 目标体积，超了自动降参数
    max_bytes: int | None = Field(default=None, ge=100_000, le=50_000_000)


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


# --------------------------------------------------------------------------- 公用检查


async def _require_parsed_url(url: str, sig: str) -> None:
    """只处理 /api/parse 签过名、并且仍然指向公网的地址。"""
    if not net.verify(url, sig):
        raise HTTPException(403, "这个地址不是解析结果里的")
    if not await is_safe_url_async(url):
        raise HTTPException(400, "不支持的地址")


def _start_job(job_type: str, fn: jobs.JobFn, ip: str, source_id: str | None = None) -> jobs.Job:
    """入队并记到该 IP 的配额上；队列满 / 配额满都返回 429 或 503。"""
    limits.jobs_per_ip.acquire(ip)
    try:
        return jobs.start(job_type, fn, source_id, owner=ip, on_release=lambda: limits.jobs_per_ip.release(ip))
    except jobs.QueueFull as err:
        limits.jobs_per_ip.release(ip)
        raise HTTPException(503, str(err), headers={"Retry-After": "30"}) from err
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


def _existing_file(path: str | None, missing: str) -> str:
    if not path or not Path(path).exists():
        raise HTTPException(404, missing)
    return path


# --------------------------------------------------------------------------- 原视频


@router.post("/api/prepare")
async def api_prepare(req: PrepareRequest, ip: str = Depends(limits.job_limit)):
    """把原视频缓存到服务端，供转换预览 / 裁剪使用。已缓存则立即返回。"""
    target = req.page_url or req.url
    if not target:
        raise HTTPException(400, "缺少视频地址")
    await _require_parsed_url(target, req.sig)
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
        await tasks.fetch_source(
            job,
            source_id=sid,
            url=req.url,
            headers=req.headers,
            page_url=req.page_url,
            format_spec=req.format_spec,
            title=req.title,
        )

    job = _start_job("prepare", fn, ip, sid)
    return {"ready": False, "job": job.view(), "source_id": sid}


async def _save_upload(file: UploadFile, dest: Path) -> None:
    size = 0
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "文件太大")
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


@router.post("/api/upload")
async def api_upload(file: UploadFile = File(...), _ip: str = Depends(limits.upload_limit)):
    """本地视频也能转 GIF / 实况。"""
    config.ensure_dirs()
    sid = uuid.uuid4().hex[:16]
    suffix = Path(file.filename or "").suffix.lower() or ".mp4"
    dest = config.UPLOADS_DIR / f"{sid}{suffix}"
    await _save_upload(file, dest)
    info = await ffmpeg.probe(dest)
    if info.duration <= 0 or info.width <= 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "这个文件不是可识别的视频")
    src = store.put(
        store.Source(
            id=sid,
            path=str(dest),
            title=Path(file.filename or "video").stem,
            duration=info.duration,
            width=info.width,
            height=info.height,
            fps=info.fps,
        )
    )
    return {"ready": True, "source": src.view()}


@router.get("/api/source/{source_id}")
async def api_source_file(source_id: str):
    return FileResponse(_source_or_404(source_id).path, media_type="video/mp4")


@router.get("/api/source/{source_id}/strip")
async def api_source_strip(source_id: str, n: int = 16):
    src = _source_or_404(source_id)
    try:
        path = await tasks.make_strip(src, frames=n)
    except Exception as err:
        raise HTTPException(500, str(err)) from err
    return FileResponse(path, media_type="image/jpeg", headers={"cache-control": "private, max-age=3600"})


# --------------------------------------------------------------------------- 起任务


@router.post("/api/convert")
async def api_convert(req: ConvertRequest, ip: str = Depends(limits.job_limit)):
    src = _source_or_404(req.source_id)
    opts = tasks.ConvertOptions(
        start=req.start,
        end=req.end,
        fps=req.fps,
        width=req.width,
        dither=req.dither,
        speed=req.speed,
        key_time=req.key_time,
        max_bytes=req.max_bytes,
    )

    async def fn(job: jobs.Job) -> None:
        await tasks.convert(job, src=src, fmt=req.format, opts=opts)

    return _start_job(req.format, fn, ip, src.id).view()


@router.post("/api/download")
async def api_download(req: DownloadRequest, ip: str = Depends(limits.job_limit)):
    """需要服务端合并的清晰度：先下载再给文件。"""
    await _require_parsed_url(req.page_url, req.sig)

    async def fn(job: jobs.Job) -> None:
        await tasks.download_for_user(
            job, page_url=req.page_url, format_spec=req.format_spec, title=req.title, ext=req.ext
        )

    return _start_job("download", fn, ip).view()


@router.post("/api/live")
async def api_live(req: LiveRequest, ip: str = Depends(limits.job_limit)):
    """平台自带的实况图（原图 + 短视频）直接打包，不经过裁剪。"""
    for it in req.items:
        await _require_parsed_url(it.image_url, it.image_sig)
        await _require_parsed_url(it.video_url, it.video_sig)
    items = [it.model_dump() for it in req.items]

    async def fn(job: jobs.Job) -> None:
        await tasks.pair_live(job, items=items, fmt=req.format, title=req.title)

    return _start_job("live", fn, ip).view()


# --------------------------------------------------------------------------- 任务查询


@router.get("/api/jobs/{job_id}")
async def api_job(job_id: str):
    return _job_or_404(job_id).view()


@router.delete("/api/jobs/{job_id}")
async def api_job_cancel(job_id: str, request: Request):
    job = _job_or_404(job_id)
    if job.owner and job.owner != limits.client_ip(request):
        raise HTTPException(403, "只能取消自己的任务")
    return {"cancelled": jobs.cancel(job_id)}


@router.get("/api/jobs/{job_id}/file")
async def api_job_file(job_id: str, inline: int = 0):
    job = _job_or_404(job_id)
    path = _existing_file(job.result_path if job.status == "done" else None, "结果还没准备好")
    media_type = _MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")
    if inline:
        return FileResponse(path, media_type=media_type)
    return FileResponse(path, media_type=media_type, filename=job.filename or Path(path).name)


@router.get("/api/jobs/{job_id}/preview")
async def api_job_preview(job_id: str):
    """实况照片的封面图（zip 里的 JPG）。"""
    job = _job_or_404(job_id)
    return FileResponse(_existing_file(job.extra.get("preview_path"), "没有预览"), media_type="image/jpeg")


@router.get("/api/jobs/{job_id}/video")
async def api_job_video(job_id: str):
    """实况照片的 MOV 部分，用于页面上按住预览。"""
    job = _job_or_404(job_id)
    return FileResponse(_existing_file(job.extra.get("mov_path"), "没有视频"), media_type="video/quicktime")
