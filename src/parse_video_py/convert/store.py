"""原视频缓存：同一个来源只下载一次，GIF / 实况多次转换都复用它。"""
from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

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


def known_paths() -> set[Path]:
    """登记在册的原视频和缩略图条；清孤儿时要绕开。"""
    known: set[Path] = set()
    for s in _sources.values():
        known.add(Path(s.path).resolve())
        if s.strip_path:
            known.add(Path(s.strip_path).resolve())
    return known


def _size(p: Path) -> int:
    try:
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return p.stat().st_size
    except OSError:
        return 0


def _remove(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)


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


_DATA_DIRS = (config.SOURCES_DIR, config.OUTPUTS_DIR, config.UPLOADS_DIR)


def disk_usage() -> int:
    return sum(_size(f) for d in _DATA_DIRS if d.exists() for f in d.iterdir())


def _orphans(known: Iterable[Path]) -> list[tuple[Path, float]]:
    """三个目录里没登记在册的文件 / 目录（进程重启后内存里的登记就没了，任务失败也会留下半成品）。"""
    known = set(known)
    found = []
    for d in _DATA_DIRS:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.resolve() in known:
                continue
            try:
                found.append((f, f.stat().st_mtime))
            except OSError:
                continue
    return found


def sweep_orphans(known: Iterable[Path]) -> int:
    """删掉超过 TTL 的孤儿；正在写的文件 mtime 一直在更新，不会被误删。返回删掉的个数。"""
    now = time.time()
    # 任务最长跑 JOB_TIMEOUT 秒；TTL 被调得比它还短时也别碰还在跑的任务
    floor = config.JOB_TIMEOUT_SECONDS * 2
    max_age = {
        config.OUTPUTS_DIR: max(config.JOB_TTL_SECONDS, floor),
        config.SOURCES_DIR: max(config.SOURCE_TTL_SECONDS, floor),
        config.UPLOADS_DIR: max(config.SOURCE_TTL_SECONDS, floor),
    }
    removed = 0
    for f, mtime in _orphans(known):
        if now - mtime > max_age.get(f.parent, floor):
            _remove(f)
            removed += 1
    return removed


def enforce_quota(known: Iterable[Path] = ()) -> int:
    """data/ 超过配额时先按最久未用删原视频，还不够就删最旧的孤儿；返回删掉的个数。"""
    used = disk_usage()
    removed = 0
    target = config.DISK_QUOTA_BYTES * 0.8
    if used <= config.DISK_QUOTA_BYTES:
        return 0
    for sid, src in sorted(_sources.items(), key=lambda kv: kv[1].created_at):
        size = _size(Path(src.path))
        _drop(sid)
        removed += 1
        used -= size
        if used <= target:
            return removed
    # 太新的孤儿多半是正在写的半成品（结果文件要到任务结束才登记），放过
    fresh = time.time() - config.JOB_TIMEOUT_SECONDS
    for f, mtime in sorted(_orphans(known), key=lambda x: x[1]):
        if mtime > fresh:
            break
        used -= _size(f)
        _remove(f)
        removed += 1
        if used <= target:
            break
    return removed
