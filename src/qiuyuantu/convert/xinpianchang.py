"""新片场高清清单下载：重新读取官方资源地址，不依赖 yt-dlp 再次请求作品网页。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from ..parser import manifests, mp4_probe
from ..parser.xinpianchang import XinPianChang
from ..utils import current_source, run_ytdlp
from . import config
from .fetch import ytdlp_download


def handles_format(page_url: str, format_spec: str) -> bool:
    return urlparse(page_url).hostname in {"www.xinpianchang.com", "xinpianchang.com"} and format_spec.startswith(
        "xpc:"
    )


async def download(
    page_url: str,
    format_spec: str,
    out_dir: Path,
    stem: str,
    report: Callable[[float, str], None],
    *,
    fit: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    parser = XinPianChang()
    token = current_source.set("xinpianchang")
    path = None
    try:
        _, resources = await parser.video_resources(page_url)
        formats = await parser.stream_formats(resources, page_url)
        selected = next((fmt for fmt in formats if fmt.format_spec == format_spec), None)
        if selected is None:
            raise ValueError("来源当前未提供所选清晰度，请重新解析后选择")
        kind = selected.format_spec.split(":")[1]
        spec = (
            f"bv*[height<={config.SOURCE_MAX_HEIGHT}]+ba/b[height<={config.SOURCE_MAX_HEIGHT}]"
            if fit
            else manifests.selector(selected)
        )
        path = await run_ytdlp(
            ytdlp_download,
            resources[kind]["url"],
            spec,
            out_dir,
            stem,
            report=report,
            cancelled=cancelled,
            fit=fit,
            http_headers={"User-Agent": parser.ua("windows"), "Referer": page_url, "Range": "bytes=0-"},
        )
        if not fit:
            metadata = await asyncio.to_thread(mp4_probe.local_metadata, path, peak_fps=kind == "hls")
            if metadata is None:
                raise ValueError("新片场下载文件没有有效的 MP4 视频轨道")
            actual, _ = metadata
            if (selected.width and actual.width != selected.width) or (
                selected.height and actual.height != selected.height
            ):
                raise ValueError("来源返回的文件与所选清晰度不一致，请重新解析")
            if selected.fps and actual.fps < selected.fps - 1:
                raise ValueError("来源返回的文件帧率低于所选档位，请重新解析")
        return path
    except BaseException:
        if path:
            path.unlink(missing_ok=True)
        raise
    finally:
        current_source.reset(token)
