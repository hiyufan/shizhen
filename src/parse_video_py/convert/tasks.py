"""后台任务本体：准备原视频、服务端下载、转 GIF / 实况 / 动态照片、平台自带实况的打包。

每个公开函数都是一个 jobs.Job 的执行体：进度写进 job，产物路径和文件名也记在 job 上。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import shutil
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from . import bili, config, ffmpeg, livephoto, store
from .fetch import MERGE_FORMAT, download_source, fetch_bytes, ytdlp_download
from .jobs import Job
from .net import safe_filename

# --------------------------------------------------------------------------- 原视频与下载


async def _ytdlp(job: Job, page_url: str, format_spec: str, out_dir: Path, stem: str) -> Path:
    def report(progress: float, message: str) -> None:
        job.set(progress=progress, message=message)

    if bili.handles(page_url):
        # B 站网页对海外机房 IP 一律 412，yt-dlp 打不开，走解析用的 API 自己下（见 bili.py）
        return await bili.download(page_url, format_spec, out_dir, stem, report)
    return await asyncio.to_thread(
        ytdlp_download,
        page_url,
        format_spec,
        out_dir,
        stem,
        report=report,
        cancelled=lambda: job.abort,
    )


def _source_format(format_spec: str) -> str:
    """转换用的原视频不必太清晰：默认限制在 SOURCE_MAX_HEIGHT 以内，省下载时间。"""
    if format_spec:
        return format_spec
    height = config.SOURCE_MAX_HEIGHT
    return f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}]/{MERGE_FORMAT}"


async def _download_source_file(
    job: Job, source_id: str, url: str, headers: dict[str, str], page_url: str, format_spec: str
) -> Path:
    if page_url:
        return await _ytdlp(job, page_url, _source_format(format_spec), config.SOURCES_DIR, source_id)
    if not url:
        raise ValueError("缺少视频地址")

    def on_bytes(done: int, total: int) -> None:
        if total:
            job.set(progress=min(0.95, done / total) * 0.9, message="原视频搬运中")

    path = config.SOURCES_DIR / f"{source_id}.mp4"
    await download_source(url, path, headers, on_bytes)
    return path


async def fetch_source(
    job: Job,
    *,
    source_id: str,
    url: str = "",
    headers: dict[str, str] | None = None,
    page_url: str = "",
    format_spec: str = "",
    title: str = "",
) -> store.Source:
    """把原视频拉到本地并探测时长 / 尺寸，供转换预览使用。直链给 url，yt-dlp 站点给 page_url。"""
    if existing := store.get(source_id):
        job.set(progress=1.0, message="搬完了")
        job.extra = existing.view()
        return existing

    config.ensure_dirs()
    job.set(progress=0.02, message="原视频搬运中")
    path = await _download_source_file(job, source_id, url, headers or {}, page_url, format_spec)

    job.set(progress=0.95, message="看看这段视频什么来头")
    info = await ffmpeg.probe(path)
    if info.duration <= 0 or info.width <= 0:
        raise RuntimeError("下载到的文件不是可用的视频")
    src = store.put(
        store.Source(
            id=source_id,
            path=str(path),
            title=title,
            duration=info.duration,
            width=info.width,
            height=info.height,
            fps=info.fps,
        )
    )
    job.extra = src.view()
    return src


async def download_for_user(job: Job, *, page_url: str, format_spec: str, title: str, ext: str = "mp4") -> None:
    """需要服务端合并的清晰度（YouTube 1080p 等）走这里，结果直接给用户下载。"""
    config.ensure_dirs()
    path = await _ytdlp(job, page_url, format_spec, config.OUTPUTS_DIR, f"dl_{job.id}")
    job.result_path = str(path)
    job.filename = safe_filename(title, path.suffix.lstrip(".") or ext, "video")


async def make_strip(src: store.Source, frames: int = 16) -> str:
    """修剪条背景用的一排缩略图，做一次就记在原视频上。"""
    if src.strip_path and Path(src.strip_path).exists():
        return src.strip_path
    config.ensure_dirs()
    out = config.SOURCES_DIR / f"{src.id}_strip.jpg"
    await ffmpeg.filmstrip(src.path, str(out), duration=src.duration, frames=frames)
    src.strip_path = str(out)
    return src.strip_path


# --------------------------------------------------------------------------- 转换：公用


@dataclass(frozen=True)
class ConvertOptions:
    """转换参数。fps / width / dither / speed / max_bytes 只对 GIF 有用，key_time 只对实况 / 动态照片有用。"""

    start: float = 0.0
    end: float | None = None
    fps: int = 12
    width: int = 480
    dither: str = "bayer"
    speed: float = 1.0
    key_time: float | None = None
    max_bytes: int | None = None  # GIF 目标体积，超了自动降参数重做


def _clamp_range(src: store.Source, start: float, end: float | None, max_len: float) -> tuple[float, float]:
    """把用户选的片段限制在视频范围和格式的时长上限内；返回 (起点, 时长)。"""
    start = max(0.0, min(float(start or 0.0), max(src.duration - 0.1, 0.0)))
    end = src.duration if end is None else float(end)
    end = max(start + 0.1, min(end, src.duration))
    end = min(end, start + max_len)
    return start, end - start


def _exif_now() -> str:
    return dt.datetime.now().strftime("%Y:%m:%d %H:%M:%S")


def _zip(bundle: Path, entries: list[tuple[Path, str]]) -> None:
    """不压缩（JPG / MOV 本来就压过了），只是装在一起。"""
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as zf:
        for path, name in entries:
            zf.write(path, name)


def _progress_from(job: Job, base: float, span: float) -> Callable[[float], Awaitable[None]]:
    """ffmpeg 报 0~1 的进度，映射到任务进度的 [base, base + span]。"""

    async def report(frac: float) -> None:
        job.set(progress=base + frac * span)

    return report


# --------------------------------------------------------------------------- 转换：GIF

GIF_FIT_ATTEMPTS = 5
# 压体积时依次尝试的降级，前面的伤画质少：(哪个参数, 降到多少)。宽度那步按需要算，降到的是下限。
# 实测（抖音 / B 站片段，有序抖动）：颜色 256→128 约 -19%、→64 约 -35%；帧率 12→10 约 -16%、→8 约 -31%；
# 体积大致和宽度的 1.7 次方成正比。颜色减半在抖动下几乎看不出来，所以最先降
_GIF_LADDER = (("colors", 128), ("fps", 10), ("width", 200), ("colors", 64), ("fps", 8), ("width", 160))
_COLOR_FACTOR = {128: 0.81, 64: 0.65}


def _shrink_gif(width: int, fps: int, colors: int, ratio: float) -> tuple[int, int, int]:
    """GIF 超了目标体积（ratio = 目标 / 实际）时，按 _GIF_LADDER 的顺序估出下一组参数，
    尽量一次压进去。只往下降：用户自己设得比下限还低的不会被抬高。"""
    need = ratio * 0.92  # 估算有误差，留点余量，免得刚好卡在线上又多跑一轮
    for knob, floor in _GIF_LADDER:
        if need >= 1:
            break
        if knob == "colors" and colors > floor:
            need /= _COLOR_FACTOR[floor] / _COLOR_FACTOR.get(colors, 1.0)
            colors = floor
        elif knob == "fps" and fps > floor:
            need /= (floor / fps) ** 0.9
            fps = floor
        elif knob == "width" and width > floor:
            new = max(floor, min(width, int(width * need ** (1 / 1.7) / 8) * 8))
            need /= (new / width) ** 1.7
            width = new
    return width, fps, colors


async def _make_gif(job: Job, src: store.Source, opts: ConvertOptions, stem: str) -> None:
    """给了 max_bytes（比如微信表情要 1MB 以内才自动播放）时，帧率和宽度只是上限：
    做出来超了就按超出的比例降参数重做，最多 GIF_FIT_ATTEMPTS 次，压不进去就交最小的那份。"""
    start, duration = _clamp_range(src, opts.start, opts.end, config.GIF_MAX_SECONDS)
    params = (max(120, min(960, int(opts.width))), max(4, min(30, int(opts.fps))), 256)
    out = config.OUTPUTS_DIR / f"{job.id}.gif"
    job.set(progress=0.05, message="GIF 一帧一帧画着呢")
    for attempt in range(GIF_FIT_ATTEMPTS):
        width, fps, colors = params
        base = min(0.8, 0.05 + 0.2 * attempt)
        await ffmpeg.make_gif(
            src.path,
            str(out),
            start=start,
            duration=duration,
            fps=fps,
            width=width,
            dither=opts.dither,
            speed=opts.speed,
            colors=colors,
            on_progress=_progress_from(job, base, 0.95 - base),
        )
        size = out.stat().st_size
        if not opts.max_bytes or size <= opts.max_bytes:
            break
        smaller = _shrink_gif(width, fps, colors, opts.max_bytes / size)
        if smaller == params or attempt == GIF_FIT_ATTEMPTS - 1:
            break
        params = smaller
        job.set(message=f"{size / 1e6:.1f} MB 有点胖，瘦个身重来：宽 {smaller[0]} px、{smaller[1]} fps")

    width, fps, colors = params
    fits = None if not opts.max_bytes else size <= opts.max_bytes
    job.extra = {"width": width, "fps": fps, "colors": colors, "max_bytes": opts.max_bytes, "fits": fits}
    job.result_path = str(out)
    job.filename = f"{stem}.gif"
    job.preview = f"/api/jobs/{job.id}/file?inline=1"


# --------------------------------------------------------------------------- 转换：实况 / 动态照片


def _live_window(src: store.Source, opts: ConvertOptions) -> tuple[float, float, float]:
    """(起点, 时长, 封面帧时间)。没指定封面帧就取片段正中间。"""
    start, duration = _clamp_range(src, opts.start, opts.end, config.LIVE_MAX_SECONDS)
    if opts.key_time is None:
        return start, duration, start + duration / 2
    return start, duration, max(start, min(float(opts.key_time), start + duration - 0.05))


async def _make_livephoto(job: Job, src: store.Source, opts: ConvertOptions, stem: str) -> None:
    """iPhone 实况：一对 content.identifier 相同的 JPG + MOV，装在 zip 里。"""
    start, duration, still_at = _live_window(src, opts)
    ident = livephoto.new_identifier()
    mov = config.OUTPUTS_DIR / f"{job.id}.MOV"
    jpg = config.OUTPUTS_DIR / f"{job.id}.JPG"
    job.set(progress=0.05, message="视频下锅，小火慢炖")
    await ffmpeg.encode_segment(
        src.path,
        str(mov),
        start=start,
        duration=duration,
        container="mov",
        extra_metadata={
            "com.apple.quicktime.content.identifier": ident,
            "creation_time": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        on_progress=_progress_from(job, 0.05, 0.9),
    )
    job.set(progress=0.9, message="挑一张好看的当封面")
    await ffmpeg.extract_frame(src.path, str(jpg), at=still_at)
    livephoto.write_jpeg_identifier(jpg, ident, date=_exif_now())
    if not livephoto.mov_has_identifier(mov, ident) or livephoto.read_jpeg_identifier(jpg) != ident:
        raise RuntimeError("实况元数据写入失败")

    bundle = config.OUTPUTS_DIR / f"{job.id}_live.zip"
    name = f"IMG_{job.id[:4].upper()}"
    _zip(bundle, [(jpg, f"{name}.JPG"), (mov, f"{name}.MOV")])
    job.result_path = str(bundle)
    job.filename = f"{stem}_实况.zip"
    job.preview = f"/api/jobs/{job.id}/preview"
    job.extra = {"preview_path": str(jpg), "mov_path": str(mov), "identifier": ident}


async def _make_motionphoto(job: Job, src: store.Source, opts: ConvertOptions, stem: str) -> None:
    """安卓动态照片：一张在末尾嵌了 MP4 的 JPG（Google Motion Photo 格式）。"""
    start, duration, still_at = _live_window(src, opts)
    mp4 = config.OUTPUTS_DIR / f"{job.id}_mp.mp4"
    jpg = config.OUTPUTS_DIR / f"{job.id}_mp.jpg"
    out = config.OUTPUTS_DIR / f"{job.id}_motion.jpg"
    job.set(progress=0.05, message="视频下锅，小火慢炖")
    await ffmpeg.encode_segment(
        src.path,
        str(mp4),
        start=start,
        duration=duration,
        container="mp4",
        on_progress=_progress_from(job, 0.05, 0.9),
    )
    job.set(progress=0.9, message="把视频塞进照片里")
    try:
        await ffmpeg.extract_frame(src.path, str(jpg), at=still_at)
        livephoto.write_jpeg_identifier(jpg, livephoto.new_identifier(), date=_exif_now())
        livephoto.write_motion_photo(jpg, mp4, out, presentation_us=int((still_at - start) * 1_000_000))
    finally:
        mp4.unlink(missing_ok=True)
        jpg.unlink(missing_ok=True)
    job.result_path = str(out)
    job.filename = f"MVIMG_{stem}.jpg"
    job.preview = f"/api/jobs/{job.id}/file?inline=1"


_MAKERS = {"gif": _make_gif, "livephoto": _make_livephoto, "motionphoto": _make_motionphoto}


async def convert(job: Job, *, src: store.Source, fmt: str, opts: ConvertOptions | None = None) -> None:
    """把原视频的一段做成 GIF / 实况照片（iPhone）/ 动态照片（安卓）。"""
    maker = _MAKERS.get(fmt)
    if maker is None:
        raise ValueError(f"不支持的格式: {fmt}")
    config.ensure_dirs()
    await maker(job, src, opts or ConvertOptions(), safe_filename(src.title, "", "clip")[:40])


# --------------------------------------------------------------------------- 平台自带的实况


def _to_jpeg(path: Path) -> Path:
    """平台给的可能是 webp / png / heic，实况配对只认 JPEG。返回 .jpg 文件，原文件不留。"""
    from PIL import Image

    out = path.with_suffix(".jpg")
    with Image.open(path) as im:
        if im.format == "JPEG":
            return path.rename(out)
        im.convert("RGB").save(out, "JPEG", quality=92)
    path.unlink(missing_ok=True)
    return out


async def _remux_live(video: Path, mov: Path, ident: str) -> None:
    """平台的实况视频直接换成带 identifier 的 MOV，不重编码；少数不是 H.264 的才重编码一次。"""
    try:
        await ffmpeg.remux_live(str(video), str(mov), identifier=ident)
    except RuntimeError:
        info = await ffmpeg.probe(video)
        await ffmpeg.encode_segment(
            str(video),
            str(mov),
            start=0,
            duration=info.duration or 3,
            extra_metadata={"com.apple.quicktime.content.identifier": ident},
        )
    if not livephoto.mov_has_identifier(mov, ident):
        raise RuntimeError("实况元数据写入失败")


async def _pair_one(work: Path, n: int, item: dict, fmt: str) -> tuple[Path, Path | None]:
    """拉一张实况的原图和短视频，配成一对。返回 (JPG, MOV)；动态照片是 (内嵌视频的 JPG, None)。"""
    img, video = work / f"{n:04d}.img", work / f"{n:04d}.mp4"
    await fetch_bytes(item["image_url"], img)
    await fetch_bytes(item["video_url"], video)
    jpg = _to_jpeg(img)
    ident = livephoto.new_identifier()
    livephoto.write_jpeg_identifier(jpg, ident, date=_exif_now())
    if fmt == "livephoto":
        mov = work / f"{n:04d}.MOV"
        await _remux_live(video, mov, ident)
        return jpg, mov
    out = work / f"MVIMG_{n:04d}.jpg"
    livephoto.write_motion_photo(jpg, video, out)
    return out, None


def _bundle_livephotos(job: Job, pairs: list[tuple[Path, Path | None]], stem: str) -> None:
    entries = []
    for i, (jpg, mov) in enumerate(pairs, 1):
        name = f"IMG_{job.id[:2].upper()}{i:02d}"
        entries += [(jpg, f"{name}.JPG"), (mov, f"{name}.MOV")]
    bundle = config.OUTPUTS_DIR / f"{job.id}_live.zip"
    _zip(bundle, entries)
    count = f"x{len(pairs)}" if len(pairs) > 1 else ""
    job.result_path = str(bundle)
    job.filename = f"{stem}_实况{count}.zip"


def _bundle_motionphotos(job: Job, photos: list[Path], stem: str) -> None:
    if len(photos) == 1:
        final = config.OUTPUTS_DIR / f"{job.id}_motion.jpg"
        photos[0].replace(final)
        job.result_path = str(final)
        job.filename = f"MVIMG_{stem}.jpg"
        return
    bundle = config.OUTPUTS_DIR / f"{job.id}_motion.zip"
    _zip(bundle, [(jpg, f"MVIMG_{stem}_{i}.jpg") for i, jpg in enumerate(photos, 1)])
    job.result_path = str(bundle)
    job.filename = f"{stem}_动态照片x{len(photos)}.zip"


async def pair_live(job: Job, *, items: list[dict], fmt: str, title: str) -> None:
    """把小红书 / 抖音自带的实况（原图 + 短视频）打包成 iPhone 实况或安卓动态照片，不裁剪不重编码。"""
    config.ensure_dirs()
    stem = safe_filename(title, "", "live")[:40]
    work = config.OUTPUTS_DIR / f"{job.id}_work"
    work.mkdir(exist_ok=True)
    try:
        pairs = []
        for i, item in enumerate(items):
            job.set(progress=i / len(items), message=f"第 {i + 1}/{len(items)} 张，一张张来")
            pairs.append(await _pair_one(work, i + 1, item, fmt))
        job.set(progress=0.97, message="装箱打包中")
        if fmt == "livephoto":
            _bundle_livephotos(job, pairs, stem)
        else:
            _bundle_motionphotos(job, [jpg for jpg, _ in pairs], stem)
        job.extra = {"count": len(pairs)}
    finally:
        # 成没成都把工作目录清掉，产物已经挪到 OUTPUTS_DIR 里了
        shutil.rmtree(work, ignore_errors=True)
