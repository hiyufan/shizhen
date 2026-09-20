"""后台任务本体：拉取原视频、服务端下载、GIF / 实况转换。"""
from __future__ import annotations

import asyncio
import datetime as dt
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from . import config, ffmpeg, livephoto, store
from .jobs import Job
from .net import headers_for, safe_client, safe_filename

_MERGE_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"


def _ytdlp_base_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "ffmpeg_location": ffmpeg.ffmpeg_dir(),
        "merge_output_format": "mp4",
        "max_filesize": config.MAX_SOURCE_BYTES,
        **config.ytdlp_cookie_opts(),
    }


def _run_ytdlp_download(job: Job, page_url: str, format_spec: str, out_dir: Path, stem: str) -> Path:
    """阻塞式 yt-dlp 下载，放到线程里跑。返回最终文件路径。"""
    import yt_dlp

    result: dict[str, Optional[str]] = {"path": None}

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                job.set(progress=min(0.95, done / total) * 0.9, message="正在下载原视频")
        elif d.get("status") == "finished":
            job.set(progress=0.92, message="正在合并音视频")

    def pp_hook(d: dict) -> None:
        if d.get("status") == "finished" and d.get("postprocessor") in ("MoveFiles", "Merger"):
            info = d.get("info_dict") or {}
            result["path"] = info.get("filepath") or info.get("_filename")

    opts = {
        **_ytdlp_base_opts(),
        "format": format_spec,
        "outtmpl": str(out_dir / f"{stem}.%(ext)s"),
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=True)
    path = result["path"] or (info.get("requested_downloads") or [{}])[0].get("filepath")
    if not path or not Path(path).exists():
        candidates = sorted(out_dir.glob(f"{stem}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise RuntimeError(f"yt-dlp 没有产出文件（可能超过 {config.MAX_SOURCE_BYTES >> 20} MB 上限）")
        path = str(candidates[0])
    return Path(path)


async def _download_direct(job: Job, url: str, headers: dict[str, str], dest: Path) -> None:
    async with safe_client(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as client:
        async with client.stream("GET", url, headers=headers_for(url, headers)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            if total > config.MAX_SOURCE_BYTES:
                raise RuntimeError(f"原视频超过 {config.MAX_SOURCE_BYTES >> 20} MB，不支持转换")
            done = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if done > config.MAX_SOURCE_BYTES:
                        raise RuntimeError(f"原视频超过 {config.MAX_SOURCE_BYTES >> 20} MB，不支持转换")
                    if total:
                        job.set(progress=min(0.95, done / total) * 0.9, message="正在下载原视频")


async def fetch_source(job: Job, *, source_id: str, url: str = "", headers: dict[str, str] | None = None,
                       page_url: str = "", format_spec: str = "", title: str = "") -> store.Source:
    """把原视频拉到本地并探测时长/尺寸，供转换预览使用。"""
    if existing := store.get(source_id):
        job.set(progress=1.0, message="已就绪")
        job.extra = existing.view()
        return existing

    config.ensure_dirs()
    job.set(progress=0.02, message="正在下载原视频")
    if page_url:
        spec = format_spec or (
            f"bv*[height<={config.SOURCE_MAX_HEIGHT}][ext=mp4]+ba[ext=m4a]"
            f"/b[height<={config.SOURCE_MAX_HEIGHT}]/{_MERGE_FORMAT}"
        )
        path = await asyncio.to_thread(_run_ytdlp_download, job, page_url, spec, config.SOURCES_DIR, source_id)
    elif url:
        path = config.SOURCES_DIR / f"{source_id}.mp4"
        await _download_direct(job, url, headers or {}, path)
    else:
        raise ValueError("缺少视频地址")

    job.set(progress=0.95, message="正在读取视频信息")
    info = await ffmpeg.probe(path)
    if info.duration <= 0 or info.width <= 0:
        raise RuntimeError("下载到的文件不是可用的视频")
    src = store.put(store.Source(
        id=source_id, path=str(path), title=title, duration=info.duration,
        width=info.width, height=info.height, fps=info.fps,
    ))
    job.extra = src.view()
    return src


async def download_for_user(job: Job, *, page_url: str, format_spec: str, title: str, ext: str = "mp4") -> None:
    """需要服务端合并的清晰度（YouTube 1080p 等）走这里，结果直接给用户下载。"""
    config.ensure_dirs()
    stem = f"dl_{job.id}"
    path = await asyncio.to_thread(_run_ytdlp_download, job, page_url, format_spec, config.OUTPUTS_DIR, stem)
    job.result_path = str(path)
    job.filename = safe_filename(title, path.suffix.lstrip(".") or ext, "video")


def _clamp_range(src: store.Source, start: float, end: Optional[float], max_len: float) -> tuple[float, float]:
    start = max(0.0, min(float(start or 0.0), max(src.duration - 0.1, 0.0)))
    end = src.duration if end is None else float(end)
    end = max(start + 0.1, min(end, src.duration))
    end = min(end, start + max_len)
    return start, end - start


async def convert(job: Job, *, src: store.Source, fmt: str, start: float = 0.0, end: Optional[float] = None,
                  fps: int = 12, width: int = 480, dither: str = "bayer", speed: float = 1.0,
                  key_time: Optional[float] = None) -> None:
    config.ensure_dirs()
    out_dir = config.OUTPUTS_DIR
    stem = safe_filename(src.title, "", "clip")[:40]

    async def progress(frac: float) -> None:
        job.set(progress=0.05 + frac * 0.9)

    if fmt == "gif":
        start, dur = _clamp_range(src, start, end, config.GIF_MAX_SECONDS)
        fps = max(4, min(30, int(fps)))
        width = max(120, min(960, int(width)))
        out = out_dir / f"{job.id}.gif"
        job.set(progress=0.05, message="正在生成 GIF")
        await ffmpeg.make_gif(src.path, str(out), start=start, duration=dur, fps=fps, width=width,
                              dither=dither, speed=speed, on_progress=progress)
        job.result_path = str(out)
        job.filename = f"{stem}.gif"
        job.preview = f"/api/jobs/{job.id}/file?inline=1"
        return

    # 实况 / 动态照片：先切一段 H.264，再抽关键帧
    start, dur = _clamp_range(src, start, end, config.LIVE_MAX_SECONDS)
    still_at = start + dur / 2 if key_time is None else max(start, min(float(key_time), start + dur - 0.05))
    ident = livephoto.new_identifier()
    now = dt.datetime.now().strftime("%Y:%m:%d %H:%M:%S")
    job.set(progress=0.05, message="正在编码视频")

    if fmt == "livephoto":
        mov = out_dir / f"{job.id}.MOV"
        jpg = out_dir / f"{job.id}.JPG"
        await ffmpeg.encode_segment(
            src.path, str(mov), start=start, duration=dur, container="mov",
            extra_metadata={
                "com.apple.quicktime.content.identifier": ident,
                "creation_time": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            on_progress=progress,
        )
        job.set(progress=0.9, message="正在生成封面")
        await ffmpeg.extract_frame(src.path, str(jpg), at=still_at)
        livephoto.write_jpeg_identifier(jpg, ident, date=now)
        if not livephoto.mov_has_identifier(mov, ident) or livephoto.read_jpeg_identifier(jpg) != ident:
            raise RuntimeError("实况元数据写入失败")
        bundle = out_dir / f"{job.id}_live.zip"
        name = f"IMG_{job.id[:4].upper()}"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as zf:
            zf.write(jpg, f"{name}.JPG")
            zf.write(mov, f"{name}.MOV")
        job.result_path = str(bundle)
        job.filename = f"{stem}_实况.zip"
        job.preview = f"/api/jobs/{job.id}/preview"
        job.extra = {"preview_path": str(jpg), "mov_path": str(mov), "identifier": ident}
        return

    if fmt == "motionphoto":
        mp4 = out_dir / f"{job.id}_mp.mp4"
        jpg = out_dir / f"{job.id}_mp.jpg"
        out = out_dir / f"{job.id}_motion.jpg"
        await ffmpeg.encode_segment(src.path, str(mp4), start=start, duration=dur, container="mp4",
                                    on_progress=progress)
        job.set(progress=0.9, message="正在合成动态照片")
        await ffmpeg.extract_frame(src.path, str(jpg), at=still_at)
        livephoto.write_jpeg_identifier(jpg, ident, date=now)
        livephoto.write_motion_photo(jpg, mp4, out, presentation_us=int((still_at - start) * 1_000_000))
        mp4.unlink(missing_ok=True)
        jpg.unlink(missing_ok=True)
        job.result_path = str(out)
        job.filename = f"MVIMG_{stem}.jpg"
        job.preview = f"/api/jobs/{job.id}/file?inline=1"
        return

    raise ValueError(f"不支持的格式: {fmt}")


async def _fetch_bytes(url: str, dest: Path, headers: dict[str, str] | None = None, limit: int = 100 << 20) -> None:
    async with safe_client(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as client:
        async with client.stream("GET", url, headers=headers_for(url, headers)) as resp:
            resp.raise_for_status()
            done = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    done += len(chunk)
                    if done > limit:
                        raise RuntimeError("文件太大")
                    f.write(chunk)


def _ensure_jpeg(path: Path) -> Path:
    """平台给的可能是 webp / png / heic, 实况配对只认 JPEG。"""
    from PIL import Image

    with Image.open(path) as im:
        if im.format == "JPEG":
            return path
        out = path.with_suffix(".jpg")
        im.convert("RGB").save(out, "JPEG", quality=92)
    path.unlink(missing_ok=True)
    return out


async def pair_live(job: Job, *, items: list[dict], fmt: str, title: str) -> None:
    """把小红书 / 抖音自带的实况 (原图 + 短视频) 打包成 iPhone 实况或安卓动态照片, 不裁剪不重编码。"""
    config.ensure_dirs()
    out_dir = config.OUTPUTS_DIR
    stem = safe_filename(title, "", "live")[:40]
    work = out_dir / f"{job.id}_work"
    work.mkdir(exist_ok=True)
    results: list[tuple[Path, Optional[Path]]] = []   # (jpg, mov/None)
    total = max(1, len(items))
    for i, item in enumerate(items):
        job.set(progress=i / total, message=f"正在处理第 {i + 1}/{total} 张")
        ident = livephoto.new_identifier()
        img = work / f"{i + 1:04d}.img"
        vid = work / f"{i + 1:04d}.mp4"
        await _fetch_bytes(item["image_url"], img)
        await _fetch_bytes(item["video_url"], vid)
        jpg = _ensure_jpeg(img)
        if jpg.suffix.lower() != ".jpg":
            jpg = jpg.rename(jpg.with_suffix(".jpg"))
        livephoto.write_jpeg_identifier(jpg, ident, date=dt.datetime.now().strftime("%Y:%m:%d %H:%M:%S"))
        if fmt == "livephoto":
            mov = work / f"{i + 1:04d}.MOV"
            try:
                await ffmpeg.remux_live(str(vid), str(mov), identifier=ident)
            except RuntimeError:
                # 少数不是 H.264 的, 重编码一次
                info = await ffmpeg.probe(vid)
                await ffmpeg.encode_segment(str(vid), str(mov), start=0, duration=info.duration or 3,
                                            extra_metadata={"com.apple.quicktime.content.identifier": ident})
            if not livephoto.mov_has_identifier(mov, ident):
                raise RuntimeError("实况元数据写入失败")
            results.append((jpg, mov))
        else:
            out = work / f"MVIMG_{i + 1:04d}.jpg"
            livephoto.write_motion_photo(jpg, vid, out)
            results.append((out, None))

    job.set(progress=0.97, message="正在打包")
    if fmt == "livephoto":
        bundle = out_dir / f"{job.id}_live.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as zf:
            for i, (jpg, mov) in enumerate(results):
                name = f"IMG_{job.id[:2].upper()}{i + 1:02d}"
                zf.write(jpg, f"{name}.JPG")
                zf.write(mov, f"{name}.MOV")
        job.result_path = str(bundle)
        job.filename = f"{stem}_实况{'x' + str(len(results)) if len(results) > 1 else ''}.zip"
    elif len(results) == 1:
        final = out_dir / f"{job.id}_motion.jpg"
        results[0][0].replace(final)
        job.result_path = str(final)
        job.filename = f"MVIMG_{stem}.jpg"
    else:
        bundle = out_dir / f"{job.id}_motion.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as zf:
            for i, (jpg, _) in enumerate(results):
                zf.write(jpg, f"MVIMG_{stem}_{i + 1}.jpg")
        job.result_path = str(bundle)
        job.filename = f"{stem}_动态照片x{len(results)}.zip"
    job.extra = {"count": len(results)}
    # 清理工作目录
    for f in work.iterdir():
        f.unlink(missing_ok=True)
    work.rmdir()


async def make_strip(src: store.Source, frames: int = 16) -> str:
    if src.strip_path and Path(src.strip_path).exists():
        return src.strip_path
    config.ensure_dirs()
    out = config.SOURCES_DIR / f"{src.id}_strip.jpg"
    await ffmpeg.filmstrip(src.path, str(out), duration=src.duration, frames=frames)
    src.strip_path = str(out)
    return src.strip_path
