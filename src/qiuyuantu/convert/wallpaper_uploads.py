"""壁纸上传的有期限引用；公开结果不包含本地文件路径。"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import animated, config, ffmpeg, net, render_process, store
from .jobs import Job


@dataclass
class Upload:
    id: str
    path: Path
    directory: Path
    title: str
    suffix: str
    expires: float
    reserved: int = 0
    assets: list[dict] = field(default_factory=list)


_uploads: dict[str, Upload] = {}


def sweep() -> None:
    for key, upload in list(_uploads.items()):
        if upload.expires < time.time():
            _uploads.pop(key, None)
            upload.path.unlink(missing_ok=True)
            shutil.rmtree(upload.directory, ignore_errors=True)


def known_paths() -> set[Path]:
    return {path.resolve() for item in _uploads.values() for path in (item.path, item.directory)}


def register(path: Path, title: str, suffix: str) -> Upload:
    sweep()
    if len(_uploads) >= 30:
        raise ValueError("壁纸暂存已满，请稍后再试")
    # 原文件已经计入磁盘使用量；为提取结果和下载 ZIP 一起预留空间，防止多个任务同时超额。
    available = config.DISK_QUOTA_BYTES - store.disk_usage() - sum(item.reserved for item in _uploads.values())
    if available <= 0:
        raise ValueError("壁纸暂存空间不足，请稍后再试")
    ident = uuid.uuid4().hex
    directory = config.UPLOADS_DIR / ("wallpaper_" + ident)
    directory.mkdir()
    upload = Upload(ident, path, directory, title, suffix, time.time() + config.SOURCE_TTL_SECONDS)
    upload.reserved = min(512 << 20, available)
    _uploads[ident] = upload
    return upload


def get(ident: str) -> Upload:
    item = _uploads.get(ident)
    if not item or item.expires < time.time() or not item.directory.is_dir():
        raise ValueError("壁纸文件已过期，请重新上传")
    return item


def signature(ident: str, asset_id: str) -> str:
    return f"wallpaper:{ident}:{asset_id}"


def asset(ident: str, asset_id: str) -> tuple[Upload, Path, dict]:
    upload = get(ident)
    if asset_id == "all":
        item = {"file": "assets.zip", "name": upload.title + ".zip", "kind": "archive"}
    elif asset_id.isdigit() and int(asset_id) < len(upload.assets):
        item = upload.assets[int(asset_id)]
    else:
        raise ValueError("壁纸素材不存在")
    path = upload.directory / item["file"]
    if not path.is_file():
        raise ValueError("壁纸素材已过期，请重新上传")
    upload.expires = time.time() + max(config.JOB_TIMEOUT_SECONDS * 2, config.SOURCE_TTL_SECONDS)
    return upload, path, item


async def extract(job: Job, ident: str) -> None:
    upload = get(ident)
    upload.expires = time.time() + max(config.JOB_TIMEOUT_SECONDS * 2, config.SOURCE_TTL_SECONDS)
    job.set(message="正在提取壁纸素材")
    try:
        options = json.dumps({"suffix": upload.suffix, "budget": upload.reserved})
        await render_process.run("wallpaper", upload.path, upload.directory, options)
        manifest = json.loads((upload.directory / "manifest.json").read_text(encoding="utf-8"))
        upload.assets = manifest["assets"]
        files = []
        for item in upload.assets:
            key = str(item["id"])
            files.append({k: v for k, v in item.items() if k != "file"} | {"sig": net.sign(signature(ident, key))})
        job.extra = {
            "wallpaper_upload": ident,
            "title": upload.title,
            "assets": files,
            "skipped": manifest["skipped"],
            "sig": net.sign(signature(ident, "all")),
        }
    except BaseException:
        _uploads.pop(ident, None)
        shutil.rmtree(upload.directory, ignore_errors=True)
        raise
    finally:
        upload.reserved = 0
        upload.path.unlink(missing_ok=True)


async def prepare(job: Job, ident: str, asset_id: str) -> None:
    _, path, item = asset(ident, asset_id)
    sid = store.source_id_for("wallpaper", ident, asset_id)
    output = config.SOURCES_DIR / f"{sid}.mp4"
    alpha = output.with_suffix(".alpha.mov")
    job.set(message="正在准备壁纸素材")
    try:
        if item["kind"] == "image":
            await animated.prepare(path, output, alpha_destination=alpha)
        else:
            await asyncio.to_thread(shutil.copyfile, path, output)
        info = await ffmpeg.probe(output)
        if info.duration <= 0 or info.width <= 0:
            raise ValueError("壁纸素材没有可转换的视频")
        source = store.put(
            store.Source(
                sid,
                str(output),
                title=item["name"],
                duration=info.duration,
                width=info.width,
                height=info.height,
                fps=info.fps,
                gif_path=str(alpha) if alpha.is_file() else None,
            )
        )
        job.extra = source.view()
    except BaseException:
        output.unlink(missing_ok=True)
        alpha.unlink(missing_ok=True)
        raise
