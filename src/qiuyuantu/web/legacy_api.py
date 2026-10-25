"""上游 parse-video-py 原有的两个接口，保持原来的返回格式（{code, msg, data}），给老调用方用。"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter

from ..convert.net import is_safe_url_async
from ..parser import parse_video_id, parse_video_share_url
from ..parser.base import VideoInfo, VideoSource
from ..utils import extract_url
from .auth import SITE_AUTH

router = APIRouter()


def _ok(info: VideoInfo) -> dict:
    return {"code": 200, "msg": "解析成功", "data": dataclasses.asdict(info)}


def _error(code: int, msg: str) -> dict:
    return {"code": code, "msg": msg}


@router.get("/video/share/url/parse", dependencies=SITE_AUTH)
async def share_url_parse(url: str):
    share_url = extract_url(url)
    if share_url is None or not await is_safe_url_async(share_url):
        return _error(400, "未检测到有效的分享链接")
    try:
        return _ok(await parse_video_share_url(share_url))
    except Exception as err:  # noqa: BLE001 - 上游接口的约定：错误放在 msg 里
        return _error(500, str(err))


@router.get("/video/id/parse", dependencies=SITE_AUTH)
async def video_id_parse(source: VideoSource, video_id: str):
    try:
        return _ok(await parse_video_id(source, video_id))
    except Exception as err:  # noqa: BLE001 - 同上
        return _error(500, str(err))
