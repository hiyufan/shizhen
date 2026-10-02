"""解析接口：/api/parse 把分享文本变成可下载的地址（带签名），失败的可以 /api/feedback 反馈。"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import failures, feedback, stats
from ..convert import config, net, relay
from ..convert.net import is_safe_url_async
from ..parser import detect_source, parse_video_share_url
from ..parser.base import VideoInfo
from ..parser.errors import ParseError, classify
from ..utils import extract_url
from . import limits
from .auth import SITE_AUTH

router = APIRouter()
log = logging.getLogger("uvicorn.error")

PARSE_TIMEOUT = 90


class ParseCache:
    """爆款链接短时间被很多人贴，不必每次都去打平台。成功的留 PARSE_CACHE_SECONDS，失败的只留一分钟。"""

    FAILURE_TTL = 60

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, dict]] = {}

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()

    def get(self, key: str) -> dict | None:
        hit = self._items.get(key)
        if not hit:
            return None
        stored_at, value = hit
        ttl = config.PARSE_CACHE_SECONDS if value.get("code") == 200 else self.FAILURE_TTL
        if time.time() - stored_at > ttl:
            self._items.pop(key, None)
            return None
        return value

    def put(self, key: str, value: dict) -> None:
        if config.PARSE_CACHE_SECONDS <= 0:
            return
        if len(self._items) >= config.PARSE_CACHE_SIZE:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)
        self._items[key] = (time.time(), value)


cache = ParseCache()


# --------------------------------------------------------------------------- 结果组装


def _failure(code: int, msg: str, reason: str) -> dict:
    return {"code": code, "msg": msg, "reason": reason}


def _media_urls(data: dict) -> set[str]:
    """结果里所有前端可能拿去 /api/proxy、/api/prepare 的地址。"""
    urls = {data.get(key) for key in ("video_url", "cover_url", "music_url", "page_url", "share_url")}
    for img in data["images"]:
        urls |= {img.get("url"), img.get("live_photo_url")}
    urls |= {f.get("url") for f in data["formats"]}
    return {u for u in urls if u}


def _edge_images(data: dict) -> dict[str, str]:
    """国内图片 CDN 的图让浏览器直接从国内边缘节点拿，不绕海外服务器。"""
    # 结果会被缓存 PARSE_CACHE_SECONDS，边缘签名要比缓存活得久
    ttl = config.PARSE_CACHE_SECONDS + 3600
    images = {data.get("cover_url")} | {img.get("url") for img in data["images"]}
    return {u: edge for u in images if u and (edge := relay.edge_img_url(u, ttl))}


def _client_data(info: VideoInfo, share_url: str) -> dict:
    data = dataclasses.asdict(info)
    data["share_url"] = share_url
    # 代理和转换只认这里签过名的地址，不做开放代理
    data["sig"] = {u: net.sign(u) for u in _media_urls(data)}
    if relay.edge_img_enabled():
        data["edge"] = _edge_images(data)
    return data


async def _parse(share_url: str) -> dict:
    try:
        info = await asyncio.wait_for(parse_video_share_url(share_url), PARSE_TIMEOUT)
    except asyncio.TimeoutError:
        return _failure(504, "解析超时，请稍后再试", "timeout")
    except Exception as err:  # noqa: BLE001 - 统一归类后告诉用户
        perr = err if isinstance(err, ParseError) else classify(err)
        return _failure(500, str(perr), perr.reason)
    return {"code": 200, "msg": "解析成功", "data": _client_data(info, share_url)}


async def _handle_failure(result: dict, share_url: str, platform: str, ip: str) -> None:
    # 修得好的失败才有反馈凭证：页面据此显示「反馈这个问题」
    if ticket := feedback.make_ticket(share_url, result["reason"], platform, result["msg"]):
        result["feedback"] = ticket
    log.warning("解析失败 url=%s reason=%s msg=%s", share_url, result["reason"], result["msg"][:160])
    # 统计里只有原因没有链接，容器日志一部署就没了：真实用户的失败链接另存 7 天，/stats 上看
    if stats.counted(ip):
        await asyncio.to_thread(failures.record, share_url, platform, result["reason"], result["msg"])


def _result_source(result: dict, platform: str) -> str:
    return (result.get("data") or {}).get("source") or platform


# --------------------------------------------------------------------------- 接口


@router.get("/api/parse", dependencies=SITE_AUTH)
async def api_parse(url: str, ip: str = Depends(limits.parse_limit)):
    """解析分享文本 / 链接，返回可下载的视频、图片和清晰度选项。"""
    share_url = extract_url(url)
    if share_url is None:
        stats.record("parse", ip, ok=False, reason="unsupported")
        return _failure(400, "没有找到链接，请粘贴完整的分享内容", "unsupported")

    platform = detect_source(share_url).value
    if cached := cache.get(share_url):
        stats.record("parse", ip, source=_result_source(cached, platform), ok=cached["code"] == 200, reason="cache")
        return cached
    if not await is_safe_url_async(share_url):
        stats.record("parse", ip, source=platform, ok=False, reason="unsupported")
        return _failure(400, "不支持这个地址", "unsupported")

    started = time.monotonic()
    result = await _parse(share_url)
    ok = result["code"] == 200
    if not ok:
        await _handle_failure(result, share_url, platform, ip)
    stats.record(
        "parse",
        ip,
        source=_result_source(result, platform),
        ok=ok,
        reason=result.get("reason", ""),
        ms=(time.monotonic() - started) * 1000,
    )
    cache.put(share_url, result)
    return result


class FeedbackRequest(BaseModel):
    ticket: str = Field(max_length=4000)
    email: str = Field(default="", max_length=254)


@router.post("/api/feedback", dependencies=SITE_AUTH)
async def api_feedback(req: FeedbackRequest, ip: str = Depends(limits.feedback_limit)):
    """解析失败后用户点「反馈这个问题」：凭证证明这条链接确实在我们这儿失败过；邮箱选填，加密存本机。"""
    if not feedback.enabled():
        raise HTTPException(404, "Not Found")
    ticket = feedback.read_ticket(req.ticket)
    if ticket is None:
        raise HTTPException(400, "反馈凭证无效或已过期，重新解析一次再反馈")
    email = req.email.strip()
    if email and not feedback.valid_email(email):
        raise HTTPException(400, "邮箱格式不对")
    duplicate = await asyncio.to_thread(feedback.add_report, ticket, email, stats.hash_ip(ip))
    return {"ok": True, "duplicate": duplicate, "email": bool(email)}
