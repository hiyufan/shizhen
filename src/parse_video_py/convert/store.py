"""原视频缓存：同一个来源只下载一次，GIF / 实况多次转换都复用它。"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config


@dataclass
class Source:
    id: str
    path: str
    title: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    strip_path: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def view(self) -> dict:
        return {
            "id": self.id, "title": self.title, "duration": round(self.duration, 3),
            "width": self.width, "height": self.height, "fps": self.fps,
        }


_sources: dict[str, Source] = {}


def source_id_for(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def get(source_id: str) -> Optional[Source]:
    src = _sources.get(source_id)
    if src and Path(src.path).exists():
        src.created_at = time.time()  # 用到就续期
        return src
    if src:
        _sources.pop(source_id, None)
    return None


def put(src: Source) -> Source:
    _sources[src.id] = src
    return src


def _drop(sid: str) -> None:
    src = _sources.pop(sid, None)
    if src:
        for p in (src.path, src.strip_path):
            if p:
                Path(p).unlink(missing_ok=True)


def sweep() -> None:
    cutoff = time.time() - config.SOURCE_TTL_SECONDS
    for sid, src in list(_sources.items()):
        if src.created_at < cutoff:
            _drop(sid)


def disk_usage() -> int:
    total = 0
    for d in (config.SOURCES_DIR, config.OUTPUTS_DIR, config.UPLOADS_DIR):
        if d.exists():
            total += sum(f.stat().st_size for f in d.iterdir() if f.is_file())
    return total


def enforce_quota() -> int:
    """data/ 超过配额时按最久未用的原视频先删；返回删掉的个数。"""
    used = disk_usage()
    removed = 0
    if used <= config.DISK_QUOTA_BYTES:
        return 0
    for sid, src in sorted(_sources.items(), key=lambda kv: kv[1].created_at):
        size = Path(src.path).stat().st_size if Path(src.path).exists() else 0
        _drop(sid)
        removed += 1
        used -= size
        if used <= config.DISK_QUOTA_BYTES * 0.8:
            break
    # 没登记在册的孤儿文件（异常退出留下的）也一起清
    if used > config.DISK_QUOTA_BYTES:
        known = {Path(s.path) for s in _sources.values()} | {Path(s.strip_path) for s in _sources.values() if s.strip_path}
        orphans = sorted((f for f in config.SOURCES_DIR.iterdir() if f.is_file() and f not in known),
                         key=lambda f: f.stat().st_mtime)
        for f in orphans:
            used -= f.stat().st_size
            f.unlink(missing_ok=True)
            removed += 1
            if used <= config.DISK_QUOTA_BYTES * 0.8:
                break
    return removed
