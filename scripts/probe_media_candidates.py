"""匿名素材只读探测：Telegram、视频号与 Pixiv 元数据及离线响应检查。

python scripts/probe_media_candidates.py telegram https://t.me/addstickers/HotCherry
python scripts/probe_media_candidates.py channels https://weixin.qq.com/sph/分享编号
python scripts/probe_media_candidates.py channels /path/to/response.json
python scripts/probe_media_candidates.py pixiv https://www.pixiv.net/artworks/151302250

不读取账号 Cookie 或 Bot 凭证；报告不包含令牌、密钥或完整媒体地址。元数据成功不代表媒体已下载。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse


async def telegram_preview(url: str) -> dict:
    from qiuyuantu.parser.telegram import Telegram

    info = await Telegram().parse_share_url(url)
    return {
        "candidate": "telegram",
        "title": info.title,
        "catalog": "Stickers.wiki",
        "catalog_entries": len(info.assets),
        "formats": sorted({asset.ext for asset in info.assets}),
        "current_telegram_version_verified": False,
        "download_verified": False,
    }


async def channels_preview(url: str) -> dict:
    from qiuyuantu.parser.wechat_channels import WeChatChannels

    info = await WeChatChannels().parse_share_url(url)
    return {
        "candidate": "channels",
        "has_cover": bool(info.cover_url),
        "images": len(info.images),
        "has_public_video": bool(info.video_url),
        "notice": info.notice,
        "download_verified": False,
    }


def pixiv_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.fullmatch(r"/(?:[a-z]{2}/)?artworks/([0-9]{1,12})/?", parsed.path)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"pixiv.net", "www.pixiv.net"}
        or parsed.username
        or parsed.port
        or not match
    ):
        raise ValueError("请提供 Pixiv 作品链接")
    return match[1]


def _pixiv_zip(url: str, ident: str) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "i.pximg.net"
        and not parsed.username
        and not parsed.port
        and bool(re.fullmatch(re.escape(ident) + r"_ugoira[0-9]+x[0-9]+\.zip", parsed.path.rsplit("/", 1)[-1]))
    )


def pixiv_response(ident: str, info: dict, animation: dict) -> dict:
    body = info.get("body") or {}
    if info.get("error") is not False or not isinstance(body, dict) or body.get("id") != ident:
        raise ValueError("Pixiv 作品身份未核实")
    result = {
        "candidate": "pixiv",
        "id": ident,
        "illust_type": body.get("illustType"),
        "pages": body.get("pageCount"),
        "requires_age_verification": body.get("xRestrict") != 0,
        "download_verified": False,
    }
    if body.get("illustType") != 2:
        return result
    meta = animation.get("body") or {}
    if animation.get("error") is not False or not isinstance(meta, dict):
        raise ValueError("Pixiv 没有提供动画元数据")
    frames = meta.get("frames") or []
    if not isinstance(frames, list) or not frames or len(frames) > 600:
        raise ValueError("帧列表缺失或超过研究原型上限")
    delays = [_pixiv_delay(row) for row in frames]
    return result | {
        "frames": len(frames),
        "duration_ms": sum(delays),
        "variable_frame_timing": len(set(delays)) > 1,
        "has_preview_zip": _pixiv_zip(meta.get("src"), ident),
        "has_larger_zip": _pixiv_zip(meta.get("originalSrc"), ident),
        "actual_frame_dimensions_verified": False,
    }


def _pixiv_delay(row: dict) -> int:
    if not isinstance(row, dict) or not re.fullmatch(r"[0-9]{6}\.(?:jpg|png)", str(row.get("file", ""))):
        raise ValueError("帧文件名无效")
    delay = row.get("delay")
    if type(delay) is not int or not 0 < delay <= 60_000:
        raise ValueError("帧时长无效")
    return delay


async def pixiv_preview(url: str) -> dict:
    from qiuyuantu.utils import create_async_client

    ident = pixiv_id(url)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.pixiv.net/"}
    base = "https://www.pixiv.net/ajax/illust/" + ident
    async with create_async_client(timeout=20) as client:
        response = await client.get(base, headers=headers)
        response.raise_for_status()
        info = response.json()
        animation = {}
        if isinstance(info.get("body"), dict) and info["body"].get("illustType") == 2:
            response = await client.get(base + "/ugoira_meta", headers=headers)
            response.raise_for_status()
            animation = response.json()
    return pixiv_response(ident, info, animation)


def channels_response(response: dict) -> dict:
    data = response
    for _ in range(3):
        if not isinstance(data.get("data"), dict):
            break
        data = data["data"]
    if data.get("playable_url"):
        parsed = urlparse(data["playable_url"])
        query = parse_qs(parsed.query)
        return {
            "candidate": "channels",
            "stage": "feed_lookup_required",
            "official_preview_host": parsed.hostname == "channels.weixin.qq.com",
            "has_export_id": bool(query.get("eid")),
            "has_general_token": bool(query.get("token")),
            "download_verified": False,
        }
    feed = data.get("feedInfo") or {}
    videos = [feed.get("videoUrl")]
    videos.extend((feed.get(codec) or {}).get("videoUrl") for codec in ("h264VideoInfo", "h265VideoInfo"))
    desktop = (data.get("objectDesc") or {}).get("media") or []
    return {
        "candidate": "channels",
        "stage": "feed_metadata" if feed or desktop else "unrecognized_or_restricted",
        "video_variants": len({url for url in videos if url}),
        "pictures": len(feed.get("picInfo") or []),
        "desktop_media": len(desktop),
        "encrypted_media": sum(bool(item.get("decodeKey")) for item in desktop),
        "download_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", choices=("telegram", "channels", "pixiv"))
    parser.add_argument("input", help="Telegram / 视频号 / Pixiv URL，或视频号本地 JSON")
    args = parser.parse_args()
    try:
        if args.candidate == "channels" and not args.input.startswith(("https://", "http://")):
            path = Path(args.input)
            if path.stat().st_size > 2_000_000:
                raise ValueError("响应超过 2 MB，请先保留单条作品的数据")
            result = channels_response(json.loads(path.read_text()))
        else:
            with tempfile.TemporaryDirectory(prefix="qyt-candidate-") as directory:
                os.environ["PARSE_VIDEO_DATA_DIR"] = directory
                os.environ["PARSE_VIDEO_SECRET"] = "candidate-probe-only"
                probe = {"telegram": telegram_preview, "channels": channels_preview, "pixiv": pixiv_preview}[
                    args.candidate
                ]
                result = asyncio.run(probe(args.input))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # 上游异常可能包含带签名地址，摘要只保留异常类型。
        parser.exit(1, f"原型未完成：{type(exc).__name__}\n")


if __name__ == "__main__":
    main()
