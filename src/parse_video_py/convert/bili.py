"""B 站的高清档位 / 仅音频：不经过 yt-dlp，自己下 DASH 的画面和声音再合并。

yt-dlp 的 B 站提取器要先打开视频网页，而海外机房 IP 打开 www.bilibili.com/video/… 一律 412
（2026-10 实测，带 buvid、换 UA、加 Referer 都一样），1080p 下载和仅音频全挂。解析用的 API
和视频 CDN 都还能访问，所以这里用解析器同一套 API 拿分轨地址。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from ..parser.bilibili import BiliBili
from ..utils import gather_or_cancel
from . import config, ffmpeg
from .fetch import TooLarge, download_source

Report = Callable[[float, str], None]

_HEIGHT = re.compile(r"height<=(\d+)")
# 编码优先级：H.264 哪里都能放；超了上限再退到体积小一半的 H.265
_AVC, _HEVC = 7, 12


def handles(page_url: str) -> bool:
    host = urlparse(page_url).hostname or ""
    return host == "b23.tv" or host == "bilibili.com" or host.endswith(".bilibili.com")


def _bw(stream: dict) -> int:
    return int(stream.get("bandwidth") or 0)


def _urls(stream: dict) -> list[str]:
    main = stream.get("baseUrl") or stream.get("base_url")
    return [u for u in [main, *(stream.get("backupUrl") or stream.get("backup_url") or [])] if u]


def pick(dash: dict, format_spec: str, seconds: float, limit: int) -> tuple[dict | None, dict | None]:
    """按 format_spec（解析结果里给的 yt-dlp 写法）挑 (画面, 声音)。仅音频时画面是 None。

    同一高度有 H.264 / H.265 / AV1 几份：先要 H.264，估算体积超了上限换 H.265，还超就报错。
    """
    audio = max(dash.get("audio") or [], key=_bw, default=None)
    if format_spec.startswith("ba"):
        if audio is None:
            raise RuntimeError("这个视频没有单独的音轨")
        return None, audio
    m = _HEIGHT.search(format_spec)
    max_h = int(m.group(1)) if m else config.SOURCE_MAX_HEIGHT
    videos = [v for v in dash.get("video") or [] if 0 < int(v.get("height") or 0) <= max_h]
    if not videos:
        raise RuntimeError("B站没有给出这个清晰度")
    top = max(int(v["height"]) for v in videos)
    tier = [v for v in videos if int(v["height"]) == top]
    audio_bw = _bw(audio) if audio else 0

    def size(v: dict) -> float:
        return (_bw(v) + audio_bw) * seconds / 8

    ranked = sorted(tier, key=lambda v: ({_AVC: 0, _HEVC: 1}.get(v.get("codecid"), 2), -_bw(v)))
    for v in ranked:
        if size(v) <= limit:
            return v, audio
    smallest = min(size(v) for v in tier)
    raise TooLarge(
        f"这个清晰度的文件约 {smallest / 1e6:.0f} MB，超过了服务器 {limit >> 20} MB 的上限，换低一档清晰度试试"
    )


async def _fetch(stream: dict, dest: Path, on_bytes) -> None:
    """主地址不行就试备用地址（B 站每个分轨都给一两个镜像）。"""
    last: Exception | None = None
    for url in _urls(stream):
        try:
            await download_source(url, dest, {}, on_bytes)
            return
        except TooLarge:
            raise
        except Exception as err:  # noqa: BLE001 - 换下一个镜像
            last = err
    raise last or RuntimeError("B站没有给出下载地址")


async def download(page_url: str, format_spec: str, out_dir: Path, stem: str, report: Report) -> Path:
    parser = BiliBili()
    await parser._ensure_buvid()
    bvid = await parser._get_bvid_from_url(page_url)
    cid = await parser._page_cid(bvid)
    if not cid:
        raise RuntimeError("B站没有返回分P信息")
    dash, _ = await parser._play_urls(bvid, cid)
    if isinstance(dash, BaseException):
        raise dash
    data = dash.get("data") or {}
    seconds = float(data.get("timelength") or 0) / 1000 or float((data.get("dash") or {}).get("duration") or 0)
    video, audio = pick(data.get("dash") or {}, format_spec, seconds, config.MAX_SOURCE_BYTES)

    parts = [(s, out_dir / f"{stem}.{kind}.m4s") for kind, s in (("v", video), ("a", audio)) if s]
    expected = sum(_bw(s) for s, _ in parts) * seconds / 8 or 1
    got = {path: 0 for _, path in parts}  # 每条分轨各下了多少

    def tracker(path: Path):
        def on_bytes(done: int, _total: int) -> None:
            got[path] = done
            report(min(0.9, sum(got.values()) / expected * 0.9), "原视频搬运中")

        return on_bytes

    try:
        # 画面和声音在不同的 CDN 连接上，一起下：以前先下完画面再下声音，声音那段是白等的
        await gather_or_cancel(*(_fetch(stream, path, tracker(path)) for stream, path in parts))
        report(0.92, "把声音和画面缝到一起")
        if video is None:
            out = out_dir / f"{stem}.m4a"
            await ffmpeg.run(["-i", str(parts[0][1]), "-vn", "-c", "copy", str(out)])
        else:
            out = out_dir / f"{stem}.mp4"
            inputs = [arg for _, path in parts for arg in ("-i", str(path))]
            maps = ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio else [])
            await ffmpeg.run([*inputs, *maps, "-c", "copy", "-movflags", "+faststart", str(out)])
    finally:
        for _, path in parts:
            path.unlink(missing_ok=True)
    return out
