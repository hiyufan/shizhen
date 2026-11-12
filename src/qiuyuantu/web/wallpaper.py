"""本地壁纸素材的签名下载与转换入口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import stats
from ..convert import config, jobs, net, store, wallpaper_uploads
from . import limits
from .auth import SITE_AUTH
from .media import _start_job

router = APIRouter(dependencies=SITE_AUTH)
_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".zip": "application/zip",
}


def _asset(upload_id: str, asset_id: str, sig: str):
    if not net.verify(wallpaper_uploads.signature(upload_id, asset_id), sig):
        raise HTTPException(403, "这个地址不是解析结果里的")
    try:
        return wallpaper_uploads.asset(upload_id, asset_id)
    except ValueError as exc:
        raise HTTPException(410, str(exc)) from exc


@router.get("/api/wallpaper/{upload_id}/{asset_id}")
async def wallpaper_file(
    upload_id: str,
    asset_id: str,
    request: Request,
    sig: str = "",
    download: int = 0,
    _ip: str = Depends(limits.proxy_limit),
):
    _, path, item = _asset(upload_id, asset_id, sig)
    if download:
        stats.record_download(
            limits.client_ip(request), wallpaper_uploads.signature(upload_id, asset_id), source="wallpaper-engine"
        )
    filename = net.safe_filename(item["name"].rsplit(".", 1)[0], path.suffix)
    return FileResponse(
        path,
        media_type=_TYPES[path.suffix],
        filename=filename,
        content_disposition_type="attachment" if download or asset_id == "all" else "inline",
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


class PrepareRequest(BaseModel):
    upload_id: str = Field(pattern="^[a-f0-9]{32}$")
    asset_id: str = Field(pattern="^[0-9]{1,3}$")
    sig: str


@router.post("/api/wallpaper/prepare")
async def wallpaper_prepare(req: PrepareRequest, ip: str = Depends(limits.job_limit)):
    _asset(req.upload_id, req.asset_id, req.sig)
    config.ensure_dirs()
    sid = store.source_id_for("wallpaper", req.upload_id, req.asset_id)
    if source := store.get(sid):
        return {"ready": True, "source": source.view()}
    if pending := jobs.find_pending("prepare", sid):
        return {"ready": False, "job": pending.view(), "source_id": sid}

    async def prepare(job):
        await wallpaper_uploads.prepare(job, req.upload_id, req.asset_id)

    job = _start_job("prepare", prepare, ip, sid)
    return {"ready": False, "job": job.view(), "source_id": sid}
