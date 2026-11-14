"""公开动态封面准备：HLS 的网络读取留在安全客户端，FFmpeg 只读取本地文件。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urljoin, urlparse

import httpx

from ..parser.applemusic import AppleMusic, _media, artwork_variants
from ..parser.base import FormatInfo
from ..parser.hoyoplay import HoYoPlay
from ..parser.huaweimusic import HOST as HUAWEI_HOST
from ..parser.huaweimusic import HuaweiMusic, is_dynamic_cover
from ..parser.mp4_probe import local_metadata
from ..parser.music import NetEaseMusic, QQMusic
from ..parser.quality import frame_rate, number, short_side
from ..utils import current_source
from . import animated, config, ffmpeg
from .fetch import TooLarge, fetch_bytes
from .net import headers_for, safe_client

Report = Callable[[float, str], None]


def handles(url: str) -> bool:
    return urlparse(url).hostname in {"music.apple.com", "music.163.com", "y.qq.com", "hyp-api.mihoyo.com", HUAWEI_HOST}


def _attributes(text: str) -> dict[str, str]:
    return {key: value.strip('"') for key, value in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)', text)}


def _check_encryption(text: str) -> None:
    for line in text.splitlines():
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")) and _attributes(line).get("METHOD") != "NONE":
            raise ValueError("暂不支持加密的动态封面")


def stream_format(master: str, base: str) -> FormatInfo:
    _check_encryption(master)
    candidates = []
    lines = master.splitlines()
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = _attributes(line)
        resolution = re.fullmatch(r"([0-9]+)x([0-9]+)", attrs.get("RESOLUTION", ""))
        if not resolution or not any(codec in attrs.get("CODECS", "") for codec in ("avc1", "hvc1", "hev1")):
            continue
        width, height = int(resolution[1]), int(resolution[2])
        if not lines[index + 1].startswith("#"):
            candidates.append(
                FormatInfo(
                    width=width,
                    height=height,
                    fps=frame_rate(attrs.get("FRAME-RATE")),
                    bitrate=int(number(attrs.get("BANDWIDTH"))),
                    url=_media(urljoin(base, lines[index + 1])),
                )
            )
    if not candidates:
        raise ValueError("动态封面没有可用的公开 H.264 / H.265 视频")
    return max(candidates, key=lambda row: (short_side(row.width, row.height), row.fps, row.bitrate))


def _range(value: str, previous_end: int | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"([0-9]+)(?:@([0-9]+))?", value)
    if not match:
        raise ValueError("动态封面分段范围无效")
    if match[2] is None and previous_end is None:
        raise ValueError("动态封面分段缺少起始位置")
    count, offset = int(match[1]), int(match[2] or previous_end)
    if count <= 0 or offset + count > config.MAX_SOURCE_BYTES:
        raise TooLarge("动态封面超过下载上限")
    return offset, count


def media_parts(text: str, base: str) -> list[tuple[str, tuple[int, int] | None]]:
    if "#EXT-X-ENDLIST" not in text or "#EXT-X-STREAM-INF" in text:
        raise ValueError("动态封面没有提供有限长度的视频")
    _check_encryption(text)
    parts, pending, previous_url, previous_end, duration = [], "", "", None, 0.0
    for line in text.splitlines():
        if line.startswith("#EXT-X-MAP:"):
            attrs = _attributes(line)
            parts.append((_media(urljoin(base, attrs["URI"])), _range(attrs.get("BYTERANGE", ""), 0)))
        elif line.startswith("#EXTINF:"):
            duration += float(line.partition(":")[2].split(",")[0])
        elif line.startswith("#EXT-X-BYTERANGE:"):
            pending = line.partition(":")[2]
        elif line and not line.startswith("#"):
            url = _media(urljoin(base, line))
            byte_range = _range(pending, previous_end if url == previous_url else None)
            parts.append((url, byte_range))
            previous_url, previous_end = url, sum(byte_range) if byte_range else None
            pending = ""
    if not 0 < duration <= 120 or not 1 <= len(parts) <= 100:
        raise ValueError("动态封面超过时长或分段限制")
    return parts


async def _piece(url: str, path: Path, byte_range: tuple[int, int] | None, limit: int) -> None:
    if not byte_range:
        await fetch_bytes(url, path, limit=limit)
        return
    offset, count = byte_range
    if count > limit:
        raise TooLarge("动态封面超过下载上限")
    headers = headers_for(url) | {"Range": f"bytes={offset}-{offset + count - 1}"}
    async with (
        safe_client(for_url=url, follow_redirects=True, timeout=60) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        response.raise_for_status()
        expected = f"bytes {offset}-{offset + count - 1}/"
        if response.status_code != 206 or not response.headers.get("content-range", "").startswith(expected):
            raise ValueError("动态封面源站没有返回正确的视频分段")
        size = 0
        with path.open("wb") as output:
            async for chunk in response.aiter_bytes(1 << 16):
                size += len(chunk)
                if size > count:
                    raise TooLarge("动态封面分段超过预期大小")
                output.write(chunk)
        if size != count:
            raise ValueError("动态封面分段没有下载完整")


async def _apple_source(page: str, spec: str, directory: Path, report: Report) -> tuple[Path, FormatInfo]:
    parser = AppleMusic()
    header, _ = await parser.artwork(page)
    variants = artwork_variants(header)
    key = spec.removeprefix("apple-artwork:") if spec.startswith("apple-artwork:") else next(iter(variants), "")
    if key not in variants:
        raise ValueError("这条作品当前没有可用的动态封面")
    master = await parser.get_text(variants[key])
    stream = stream_format(master, variants[key])
    parts = media_parts(await parser.get_text(stream.url), stream.url)
    source, total = directory / "source.bin", 0
    with source.open("wb") as output:
        for index, (url, byte_range) in enumerate(parts):
            piece = directory / "piece.bin"
            await _piece(url, piece, byte_range, config.MAX_SOURCE_BYTES - total)
            total += piece.stat().st_size
            with piece.open("rb") as downloaded:
                while chunk := downloaded.read(1 << 16):
                    output.write(chunk)
            piece.unlink()
            report(0.9 * (index + 1) / len(parts), "动态封面分段搬运中")
    return source, stream


async def _netease_source(page: str, directory: Path, report: Report) -> Path:
    # 下载任务不在解析上下文里，要自己标上来源，网易云的接口才会照解析时一样走国内中继
    token = current_source.set("netease")
    try:
        url = await NetEaseMusic().dynamic_cover(page)
    except (httpx.HTTPError, ValueError, TypeError) as err:
        # 原因（游客身份、接口格式）只记在异常链里，用户看到的是能照着做的说法
        raise ValueError("网易云音乐暂时没有返回动态封面，请稍后再试") from err
    finally:
        current_source.reset(token)
    if not url:
        raise ValueError("这首歌当前没有动态封面")
    source = directory / "source.mp4"
    await fetch_bytes(url, source, limit=config.MAX_SOURCE_BYTES)
    report(0.9, "正在准备动态封面")
    return source


async def _remux(source: Path, destination: Path, *, shift_timestamps: bool = True) -> None:
    """只换封装：去掉音轨、把索引挪到文件头，画面原样保留。"""
    args = ["-i", str(source), "-map", "0:v:0", "-c", "copy", "-an"]
    # QQ 原片用负 DTS 表达 B 帧预解码，首帧 PTS 已经为零；平移 DTS 会给短循环额外加上起始等待。
    args += ["-avoid_negative_ts", "make_zero" if shift_timestamps else "disabled"]
    await ffmpeg.run([*args, "-movflags", "+faststart", str(destination)])


async def _qq_source(page: str, directory: Path, report: Report, *, square: bool = False) -> Path:
    token = current_source.set("qqmusic")
    try:
        url = await QQMusic().dynamic_cover(page, square=True) if square else await QQMusic().dynamic_cover(page)
    except (httpx.HTTPError, ValueError, TypeError) as err:
        raise ValueError("QQ 音乐暂时没有返回动态封面，请稍后再试") from err
    finally:
        current_source.reset(token)
    if not url:
        raise ValueError("这张专辑当前没有动态封面")
    source = directory / "source.mp4"
    await fetch_bytes(url, source, limit=config.MAX_SOURCE_BYTES)
    report(0.9, "正在准备动态封面")
    return source


async def _validate_mp4(path: Path) -> FormatInfo:
    measured = await asyncio.to_thread(local_metadata, path)
    if not measured or not measured[0].width or not measured[0].height or not 0 < measured[1] <= 120:
        raise ValueError("动态封面没有返回有效的 MP4 视频")
    return measured[0]


async def _square_cover(source: Path, destination: Path, side: int) -> None:
    # 保留原帧时序；Baseline、单参考帧兼容不支持 B 帧的解码器，减少循环时的参考帧依赖。
    await ffmpeg.run(
        [
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-vf",
            f"crop={side}:{side},setsar=1",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "14",
            "-profile:v",
            "baseline",
            "-bf",
            "0",
            "-refs",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-an",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )


async def _qq_download(page: str, spec: str, directory: Path, destination: Path, report: Report) -> None:
    source = await _qq_source(page, directory, report, square=spec == "qqmusic-artwork:square")
    expected = await _validate_mp4(source)
    if spec == "qqmusic-artwork:square":
        side = min(expected.width, expected.height) // 2 * 2
        await _square_cover(source, destination, side)
        expected = FormatInfo(width=side, height=side, fps=expected.fps)
    else:
        await _remux(source, destination, shift_timestamps=False)
    await _validate_output(destination, expected)


async def _huawei_download(page: str, directory: Path, destination: Path, report: Report) -> None:
    token = current_source.set("huaweimusic")
    try:
        info = await HuaweiMusic().parse_share_url(page)
    finally:
        current_source.reset(token)
    url = next((asset.url for asset in info.assets if asset.kind == "animation" and is_dynamic_cover(asset.url)), "")
    if not url:
        raise ValueError("这条作品当前未公开动态封面，可下载静态封面。")
    source = directory / "source.webp"
    await fetch_bytes(url, source, limit=config.MAX_SOURCE_BYTES)
    report(0.5, "正在读取动态封面的每一帧")
    # 无损中间文件保留帧时间戳，也避免编码器把 PNG 清单的 1000 Hz 时基当成播放帧率。
    await animated.prepare(
        source,
        destination,
        alpha_destination=directory / "frames.mov",
        max_frame_pixels=200_000_000,
        preserve_timing=True,
    )
    await _validate_mp4(destination)


async def _validate_output(path: Path, expected: FormatInfo) -> None:
    # HLS FRAME-RATE 是峰值；变帧率素材不能用平均值判断是否降档。
    measured = await asyncio.to_thread(local_metadata, path, peak_fps=True)
    if not measured:
        raise ValueError("动态封面没有返回有效的 MP4 视频")
    actual, _ = measured
    if (actual.width, actual.height) != (expected.width, expected.height) or actual.fps + 0.1 < expected.fps:
        raise ValueError("动态封面的实际尺寸或帧率与所选档位不一致")


async def download(page: str, spec: str, out_dir: Path, stem: str, report: Report) -> Path:
    destination = out_dir / f"{stem}.mp4"
    with TemporaryDirectory(prefix=f"{stem}-artwork-", dir=out_dir) as temporary:
        directory = Path(temporary)
        try:
            host = urlparse(page).hostname
            if host == "music.apple.com":
                source, expected = await _apple_source(page, spec, directory, report)
                await _remux(source, destination)
                await _validate_output(destination, expected)
            elif host == "music.163.com":
                await _remux(await _netease_source(page, directory, report), destination)
                await _validate_mp4(destination)
            elif host == "y.qq.com":
                await _qq_download(page, spec, directory, destination, report)
            elif host == HUAWEI_HOST:
                await _huawei_download(page, directory, destination, report)
            else:
                info = await HoYoPlay().parse_share_url(page)
                if not info.video_url:
                    raise ValueError("这个游戏当前只提供静态壁纸，可直接下载原图")
                source = directory / "source.webm"
                await fetch_bytes(info.video_url, source, limit=config.MAX_SOURCE_BYTES)
                probe = await ffmpeg.probe(source)
                if not 0 < probe.duration <= 120:
                    raise ValueError("动态壁纸超过时长限制")
                report(0.9, "正在准备动态壁纸")
                await ffmpeg.encode_segment(
                    str(source), str(destination), start=0, duration=probe.duration, container="mp4"
                )
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return destination
