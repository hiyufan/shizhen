"""把远端的视频 / 图片拉到本地：直链流式下载（大小上限、进度）、国内 CDN 直连不通时改走中继、
yt-dlp 下载并合并音视频。不关心任务队列，进度和取消都通过回调传进来。"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import httpx

from ..utils import is_cn_url, proxy_for
from . import config, ffmpeg, net, relay
from .net import headers_for, safe_client

# (已下载字节, 总字节；不知道总大小时为 0)
ByteProgress = Callable[[int, int], None]
# (0~1 的进度, 给用户看的说明)
StepProgress = Callable[[float, str], None]

_CHUNK = 1 << 16
_TIMEOUT = httpx.Timeout(30, read=120)


class TooLarge(RuntimeError):
    pass


async def _stream_to_file(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    headers: dict[str, str] | None,
    limit: int,
    too_large: str,
    on_bytes: ByteProgress | None = None,
) -> None:
    """边下边写，超过 limit 就停；不管什么原因中断，半截文件都删掉。"""
    async with client.stream("GET", url, headers=headers_for(url, headers)) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        if total > limit:
            raise TooLarge(too_large)
        done = 0
        try:
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(_CHUNK):
                    done += len(chunk)
                    if done > limit:
                        raise TooLarge(too_large)
                    f.write(chunk)
                    if on_bytes:
                        on_bytes(done, total)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise


async def download_source(url: str, dest: Path, headers: dict[str, str], on_bytes: ByteProgress) -> None:
    """用户要转换的原视频：按平台自动选代理，超过 MAX_SOURCE_BYTES 不收。"""
    limit = config.MAX_SOURCE_BYTES
    async with safe_client(for_url=url, follow_redirects=True, timeout=_TIMEOUT) as client:
        await _stream_to_file(
            client,
            url,
            dest,
            headers=headers,
            limit=limit,
            too_large=f"原视频超过 {limit >> 20} MB，不支持转换",
            on_bytes=on_bytes,
        )


# 海外服务器到国内媒体 CDN 的 TLS 握手经常整段超时（2026-10 实测 douyinpic 的图、
# 365yg / zjcdn 的实况视频都会握不上，同一 IP 的 TCP 是通的）。浏览器看图走边缘 /img
# 碰不到，实况打包要服务器自己拉原图和实况视频就挂了。这些直连失败就改走中继（实况
# 视频几百 KB，中继扛得住）；失败过的 CDN 10 分钟内直接走中继，免得每个文件都先白等
# 一轮超时。按 CDN 主域名记：同一条作品的图分在 p5-ex-… / p95-zjwztc-… 不同子域名上，
# 坏的是整条跨境链路
_RELAY_FIRST: dict[str, float] = {}
_RELAY_FIRST_SECONDS = 600


async def _fetch_once(
    url: str,
    dest: Path,
    headers: dict[str, str] | None,
    limit: int,
    *,
    via_relay: bool = False,
    connect_timeout: float = 30,
) -> None:
    route = {"transport": relay.shared_transport()} if via_relay else {"for_url": url}
    timeout = httpx.Timeout(30, connect=connect_timeout, read=120)
    async with safe_client(follow_redirects=True, timeout=timeout, **route) as client:
        await _stream_to_file(client, url, dest, headers=headers, limit=limit, too_large="文件太大")


async def fetch_bytes(url: str, dest: Path, headers: dict[str, str] | None = None, limit: int = 100 << 20) -> None:
    """实况打包要的原图和短视频。国内 CDN 直连 10 秒连不上就改走中继，并记住这个 CDN 一阵子。"""
    cdn = relay.cn_media_cdn(url) if relay.enabled() else ""
    if not cdn:
        await _fetch_once(url, dest, headers, limit)
        return
    if _RELAY_FIRST.get(cdn, 0) > time.time():
        await _fetch_once(url, dest, headers, limit, via_relay=True)
        return
    try:
        await _fetch_once(url, dest, headers, limit, connect_timeout=10)
    except httpx.TransportError:
        _RELAY_FIRST[cdn] = time.time() + _RELAY_FIRST_SECONDS
        await _fetch_once(url, dest, headers, limit, via_relay=True)


# --------------------------------------------------------------------------- yt-dlp

MERGE_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"


def _ytdlp_opts(page_url: str, format_spec: str, out_dir: Path, stem: str) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "ffmpeg_location": ffmpeg.ffmpeg_dir(),
        "merge_output_format": "mp4",
        "max_filesize": config.MAX_SOURCE_BYTES,
        "format": format_spec,
        "outtmpl": str(out_dir / f"{stem}.%(ext)s"),
        **config.ytdlp_cookie_opts(),
    }
    if proxy := proxy_for("bilibili" if is_cn_url(page_url) else "ytdlp"):
        opts["proxy"] = proxy
    return opts


class _YtdlpHooks:
    """yt-dlp 的进度回调：报进度、记下合并后的文件路径，任务取消了就让它停下来。"""

    def __init__(self, report: StepProgress, cancelled: Callable[[], bool]) -> None:
        self._report = report
        self._cancelled = cancelled
        self.path: str | None = None

    def progress(self, d: dict) -> None:
        import yt_dlp

        if self._cancelled():
            raise yt_dlp.utils.DownloadCancelled("任务已取消")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                done = d.get("downloaded_bytes") or 0
                self._report(min(0.95, done / total) * 0.9, "正在下载原视频")
        elif d.get("status") == "finished":
            self._report(0.92, "正在合并音视频")

    def postprocessor(self, d: dict) -> None:
        if d.get("status") == "finished" and d.get("postprocessor") in ("MoveFiles", "Merger"):
            info = d.get("info_dict") or {}
            self.path = info.get("filepath") or info.get("_filename")


def _downloaded_file(hooks: _YtdlpHooks, info: dict, out_dir: Path, stem: str) -> Path:
    path = hooks.path or (info.get("requested_downloads") or [{}])[0].get("filepath")
    if path and Path(path).exists():
        return Path(path)
    # 各版本 yt-dlp 报路径的地方不一样，都没报就找刚下好的那个
    candidates = sorted(out_dir.glob(f"{stem}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"yt-dlp 没有产出文件（可能超过 {config.MAX_SOURCE_BYTES >> 20} MB 上限）")
    return candidates[0]


def ytdlp_download(
    page_url: str,
    format_spec: str,
    out_dir: Path,
    stem: str,
    *,
    report: StepProgress,
    cancelled: Callable[[], bool],
) -> Path:
    """阻塞式 yt-dlp 下载，放到线程里跑。返回最终文件路径。

    任务超时 / 被取消时 asyncio 只是不再等这个线程，yt-dlp 本身还会继续下；
    所以在进度回调里看 cancelled()，是真就抛 DownloadCancelled 让它停下来。
    """
    import yt_dlp

    hooks = _YtdlpHooks(report, cancelled)
    opts = _ytdlp_opts(page_url, format_spec, out_dir, stem)
    opts.update(progress_hooks=[hooks.progress], postprocessor_hooks=[hooks.postprocessor])
    try:
        with net.public_only(), yt_dlp.YoutubeDL(opts) as ydl:  # SSRF：见 net.public_only
            info = ydl.extract_info(page_url, download=True)
    except Exception:
        # 半截的 .part / .ytdl 别留着占磁盘
        for leftover in out_dir.glob(f"{stem}.*"):
            leftover.unlink(missing_ok=True)
        raise
    return _downloaded_file(hooks, info, out_dir, stem)
