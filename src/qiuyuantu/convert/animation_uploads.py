"""动画上传暂存、Spine 动画选择与受控准备任务。"""

from __future__ import annotations

import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import animated, config, ffmpeg, spine_data, store
from .jobs import Job


@dataclass
class Upload:
    id: str
    path: Path
    title: str
    animations: list[str]
    expires: float


_uploads: dict[str, Upload] = {}


def sweep() -> None:
    now = time.time()
    for ident, upload in list(_uploads.items()):
        if upload.expires < now:
            _uploads.pop(ident, None)
            upload.path.unlink(missing_ok=True)


def known_paths() -> set[Path]:
    return {item.path.resolve() for item in _uploads.values()}


def _animations(path: Path) -> list[str]:
    with path.open("rb") as stream:
        zipped = stream.read(4) == b"PK\x03\x04"
    if not zipped:
        return []
    try:
        with zipfile.ZipFile(path) as archive:
            if "movie.spec" in archive.namelist():
                return []
        return spine_data.describe(path)
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise ValueError("动画文件结构无效，请检查 ZIP 与骨骼 JSON") from exc


def register(path: Path, title: str) -> Upload:
    sweep()
    if len(_uploads) >= 100:
        raise ValueError("动画暂存已满，请稍后再试")
    names = _animations(path)
    ident = uuid.uuid4().hex
    upload = Upload(ident, path, title, names, time.time() + config.SOURCE_TTL_SECONDS)
    _uploads[ident] = upload
    return upload


def get(ident: str) -> Upload:
    item = _uploads.get(ident)
    if not item or item.expires <= time.time() or not item.path.is_file():
        raise ValueError("动画文件已过期，请重新上传")
    return item


async def prepare(job: Job, ident: str, animation: str = "") -> None:
    upload = get(ident)
    if upload.animations and animation not in upload.animations:
        raise ValueError("请选择这份 Spine 文件中的一个动画")
    sid = store.source_id_for("animation-upload", ident, animation)
    if cached := store.get(sid):
        job.extra = cached.view()
        return
    upload.expires = time.time() + max(config.JOB_TIMEOUT_SECONDS * 2, config.SOURCE_TTL_SECONDS)
    config.ensure_dirs()
    output = config.SOURCES_DIR / f"{sid}.mp4"
    alpha = config.SOURCES_DIR / f"{sid}.alpha.mov"
    job.set(message="正在读取动画的每一帧")
    await animated.prepare(upload.path, output, alpha_destination=alpha, animation=animation)
    info = await ffmpeg.probe(output)
    if info.duration <= 0 or info.width <= 0:
        output.unlink(missing_ok=True)
        alpha.unlink(missing_ok=True)
        raise ValueError("动画没有生成可用的视频")
    source = store.put(
        store.Source(
            id=sid,
            path=str(output),
            title=upload.title,
            duration=info.duration,
            width=info.width,
            height=info.height,
            fps=info.fps,
            gif_path=str(alpha),
        )
    )
    job.extra = source.view()
