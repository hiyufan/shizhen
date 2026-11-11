"""抖音直播间：读公开的房间信息，给出各清晰度的拉流地址。

只返回地址，不录制、不经服务器转发——直播流没有终点，不适合走下载 / 代理那一套。
网页直播间地址里是 web_rid，进房接口认访客身份（首页发的 ttwid）；App 分享出来的落地页
带的是房间号，另有一个不带 Cookie 就能读的接口。两边给的流信息是同一种结构。
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from ..convert.net import safe_client
from ..utils import create_async_client
from .base import BaseParser, LiveStream, VideoAuthor, VideoInfo
from .errors import ParseError

# ("web_rid" | "room_id", 值)
LiveRef = tuple[str, str]

_HOME = "https://live.douyin.com/"
_ENTER = "https://live.douyin.com/webcast/room/web/enter/"
_REFLOW = "https://webcast.amemv.com/webcast/room/reflow/info/"
# 进房接口按网页端的参数校验；UA 与这里的浏览器版本保持一致
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
_ENTER_PARAMS = {
    "aid": "6383",
    "app_name": "douyin_web",
    "live_id": "1",
    "device_platform": "web",
    "language": "zh-CN",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "130.0.0.0",
}
_LIVING = 2

# 访客标识有效期很长，进程内留着重复用；接口不认了（返回空正文）再换
_GUEST_TTL = 6 * 3600
_guest = {"ttwid": "", "at": 0.0}

_SHARE_HOSTS = ("webcast.amemv.com", "www.iesdouyin.com", "www.douyin.com", "douyin.com")
_SHARE_ROOM = re.compile(r"/(?:webcast/reflow|share/live)/(\d+)")
# 网页房间号多数是一串数字，设了抖音号的主播用的是抖音号
_WEB_RID = r"[\w.-]+"
_WEB_ROOM = re.compile(rf"/live/({_WEB_RID})/?$")

_CODECS = {"h264": "H.264", "h265": "H.265", "bytevc1": "H.265", "hevc": "H.265", "av1": "AV1"}
# FLV 视频 tag 的编码号：7 是标准里的 H.264，12 是国内 CDN 通行的 H.265 扩展
_FLV_CODEC_IDS = {7: "H.264", 12: "H.265"}
_FLV_FOURCC = {b"avc1": "H.264", b"hvc1": "H.265", b"av01": "AV1"}
_PROBE_BYTES = 64 * 1024
_PROBE_TIMEOUT = 4.0
_META_BYTES = 8 * 1024


def live_ref(url: str) -> LiveRef | None:
    """地址指向哪个直播间；不是直播间地址返回 None。

    https://live.douyin.com/<web_rid>（只有一段路径；频道、分类页是多段的）
    https://www.douyin.com/root/live/<web_rid>  /  https://www.douyin.com/follow/live/<web_rid>
    https://webcast.amemv.com/douyin/webcast/reflow/<room_id>（App 分享短链的落地页）
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host == "live.douyin.com":
        room = parsed.path.strip("/")
        return ("web_rid", room) if re.fullmatch(_WEB_RID, room) else None
    if host not in _SHARE_HOSTS:
        return None
    if found := _SHARE_ROOM.search(parsed.path):
        return ("room_id", found.group(1))
    if host != "webcast.amemv.com" and (found := _WEB_ROOM.search(parsed.path)):
        return ("web_rid", found.group(1))
    return None


async def parse(parser: BaseParser, ref: LiveRef) -> VideoInfo:
    kind, value = ref
    if kind == "room_id":
        room = await _room_by_id(parser, value)
        owner = room.get("owner") or {}
        web_rid = str(owner.get("web_rid") or "")
    else:
        room, owner = await _room_by_web_rid(parser, value)
        web_rid = value
    return await _build(room, owner, f"{_HOME}{web_rid}" if web_rid else "")


async def _guest_id(*, fresh: bool = False) -> str:
    if not fresh and _guest["ttwid"] and time.monotonic() - _guest["at"] < _GUEST_TTL:
        return _guest["ttwid"]
    # 只要响应头：首页正文近 1 MB。HEAD 的状态码不固定（见过 404），访客标识照发，所以不看状态码
    async with create_async_client(follow_redirects=False) as client:
        response = await client.head(_HOME, headers={"User-Agent": _UA})
    for cookie in response.headers.get_list("set-cookie"):
        if found := re.match(r"ttwid=([^;]+)", cookie):
            _guest.update(ttwid=found.group(1), at=time.monotonic())
            return found.group(1)
    raise ParseError("blocked", "抖音没有给服务器发放访客身份")


async def _room_by_web_rid(parser: BaseParser, web_rid: str) -> tuple[dict, dict]:
    headers = {"User-Agent": _UA, "Referer": f"{_HOME}{web_rid}"}
    payload = None
    for fresh in (False, True):
        cookie = f"ttwid={await _guest_id(fresh=fresh)}"
        response = await parser.fetch(
            "GET", _ENTER, params={**_ENTER_PARAMS, "web_rid": web_rid}, headers={**headers, "Cookie": cookie}
        )
        if response.content.strip():
            payload = response.json()
            break
    if payload is None:
        raise ParseError("blocked", "抖音直播接口没有返回数据")
    data = payload.get("data") or {}
    rooms = data.get("data") or []
    if payload.get("status_code") or not rooms:
        raise ParseError("deleted", "没有找到这个直播间")
    return rooms[0], data.get("user") or {}


async def _room_by_id(parser: BaseParser, room_id: str) -> dict:
    payload = await parser.get_json(
        _REFLOW,
        params={"type_id": "0", "live_id": "1", "room_id": room_id, "app_id": "1128"},
        headers={"User-Agent": _UA},
    )
    room = (payload.get("data") or {}).get("room") or {}
    if payload.get("status_code") or not room:
        raise ParseError("deleted", "没有找到这个直播间")
    return room


async def _build(room: dict, owner: dict, page_url: str) -> VideoInfo:
    name = owner.get("nickname") or ""
    if room.get("status") != _LIVING:
        raise ParseError("offline", f"主播：{name}" if name else "")
    stream_url = room.get("stream_url") or {}
    pull = (stream_url.get("live_core_sdk_data") or {}).get("pull_data") or {}
    qualities = _loads(pull.get("stream_data")).get("data") or {}
    streams = _video_streams(qualities, pull.get("options") or {}) or _legacy_streams(stream_url)
    if not streams:
        raise ParseError("restricted", "这个直播间没有向访客提供画面，可能是付费或仅限 App 观看")
    streams += await _audio_streams(_main(qualities.get("ao")).get("flv") or "")
    best = streams[0]
    return VideoInfo(
        video_url="",
        cover_url=_https(_first((room.get("cover") or {}).get("url_list"))),
        title=room.get("title") or (f"{name}的直播间" if name else "抖音直播间"),
        author=VideoAuthor(
            uid=str(owner.get("sec_uid") or ""),
            name=name,
            avatar=_https(_first((owner.get("avatar_thumb") or {}).get("url_list"))),
        ),
        page_url=page_url,
        width=best.width,
        height=best.height,
        streams=streams,
        content_kind="live",
    )


def _video_streams(qualities: dict, options: dict) -> list[LiveStream]:
    """平台转码出的各清晰度，按档位从高到低。名称用平台自己的叫法（蓝光 / 超清…），没有名称的按分辨率。"""
    listed = {q.get("sdk_key"): q for q in options.get("qualities") or [] if isinstance(q, dict)}
    ranked = []
    for key, entry in qualities.items():
        main = _main(entry)
        if key == "ao" or not (main.get("flv") or main.get("hls")):
            continue
        params = _loads(main.get("sdk_params"))
        width, height = _resolution(params.get("resolution") or (listed.get(key) or {}).get("resolution"))
        stream = LiveStream(
            label=(listed.get(key) or {}).get("name") or (f"{min(width, height)}p" if width else key),
            flv_url=_https(main.get("flv") or ""),
            hls_url=_https(main.get("hls") or ""),
            width=width,
            height=height,
            fps=_number(params.get("fps")),
            bitrate=int(_number(params.get("vbitrate"))),
            codec=_CODECS.get(str(params.get("VCodec") or "").lower(), ""),
        )
        ranked.append((_number((listed.get(key) or {}).get("level")), width * height, stream))
    ranked.sort(key=lambda item: item[:2], reverse=True)
    return [stream for *_, stream in ranked]


def _legacy_streams(stream_url: dict) -> list[LiveStream]:
    """少数房间不带分档明细，只有按清晰度代号给的两张地址表；代号本身就是从高到低排的。"""
    flv, hls = stream_url.get("flv_pull_url") or {}, stream_url.get("hls_pull_url_map") or {}
    return [
        LiveStream(label=key, flv_url=_https(flv.get(key) or ""), hls_url=_https(hls.get(key) or ""))
        for key in dict.fromkeys([*flv, *hls])
    ]


async def _audio_streams(audio_flv: str) -> list[LiveStream]:
    if not audio_flv:
        return []
    audio = LiveStream(label="仅音频", flv_url=_https(audio_flv), kind="audio")
    source = await _source_stream(audio.flv_url)
    return [source, audio] if source else [audio]


async def _source_stream(audio_flv: str) -> LiveStream | None:
    """仅音频地址去掉 only_audio 就是主播推上来、平台没有转码的那一路。

    它不在平台的清晰度列表里，所以先读开头确认真有画面、顺便认出编码；
    读不到就不给，免得页面上出现打不开的地址。
    """
    parsed = urlparse(audio_flv)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "only_audio"]
    url = urlunparse(parsed._replace(query=urlencode(query)))
    if url == audio_flv:
        return None
    try:
        head = await asyncio.wait_for(_stream_head(url), _PROBE_TIMEOUT)
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None
    codec = flv_video_codec(head)
    if codec is None:
        return None
    # 推流端写的元数据里通常没有宽高，帧率和码率（kbit/s）是有的
    return LiveStream(
        label="原始推流",
        flv_url=url,
        fps=flv_meta_number(head, "framerate"),
        bitrate=int(flv_meta_number(head, "videodatarate") * 1000),
        codec=codec,
        kind="source",
    )


async def _stream_head(url: str) -> bytes:
    head = bytearray()
    async with (
        safe_client(for_url=url, timeout=_PROBE_TIMEOUT) as client,
        client.stream("GET", url, headers={"User-Agent": _UA}) as response,
    ):
        response.raise_for_status()
        async for chunk in response.aiter_raw(16 * 1024):
            head.extend(chunk)
            if len(head) >= _PROBE_BYTES:
                break
    return bytes(head)


def flv_video_codec(data: bytes) -> str | None:
    """FLV 开头第一个视频 tag 的编码；没有视频 tag 返回 None，认不出的编码返回空串。"""
    if data[:3] != b"FLV":
        return None
    pos = 13  # 文件头 9 字节 + 第一个 PreviousTagSize
    while pos + 16 <= len(data):
        size = int.from_bytes(data[pos + 1 : pos + 4], "big")
        if data[pos] & 0x1F == 9:
            first = data[pos + 11]
            if first & 0x80:  # Enhanced RTMP：首字节高位置 1，后面跟四字符编码
                return _FLV_FOURCC.get(data[pos + 12 : pos + 16], "")
            return _FLV_CODEC_IDS.get(first & 0x0F, "")
        pos += 11 + size + 4
    return None


def flv_meta_number(data: bytes, key: str) -> float:
    """onMetaData 里某个数值属性；没有或不像样返回 0。属性在 AMF0 里是「2 字节长度 + 名称 + 类型 0 + 8 字节浮点」。"""
    marker = len(key).to_bytes(2, "big") + key.encode() + b"\x00"
    at = data.find(marker, 0, _META_BYTES)
    raw = data[at + len(marker) : at + len(marker) + 8]
    if at < 0 or len(raw) < 8:
        return 0
    value = struct.unpack(">d", raw)[0]
    return value if 0 < value < 1e6 else 0


def _main(entry: object) -> dict:
    main = entry.get("main") if isinstance(entry, dict) else None
    return main if isinstance(main, dict) else {}


def _loads(text: object) -> dict:
    """接口把几段 JSON 以字符串塞在字段里；缺了或坏了按空处理，由调用方决定退路。"""
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(text) if isinstance(text, str) and text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _resolution(text: object) -> tuple[int, int]:
    found = re.fullmatch(r"(\d+)x(\d+)", str(text or ""))
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def _first(urls: object) -> str:
    return urls[0] if isinstance(urls, list) and urls and isinstance(urls[0], str) else ""


def _https(url: str) -> str:
    return "https://" + url[len("http://") :] if url.startswith("http://") else url
