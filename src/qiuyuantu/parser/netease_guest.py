"""网易云音乐的游客身份：网页和 App 给每个未登录访客自动发一个，这里只用它读动态封面。

动态封面接口不带身份时一律返回 code 301；只带设备号也不行，要的是游客或登录身份。
游客身份不对应任何账号，也不碰歌曲播放地址。整个进程共用一个：平台给的有效期是几十年，
每次解析都领新的既慢（经中继约 5 秒）又会在平台那边堆出大量游客，失效（再次 301）时才重领。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import string
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

if TYPE_CHECKING:
    from .base import BaseParser

# 网页端 weapi 的公开参数（写在 music.163.com 的前端脚本里），不是密钥
_ID_KEY = "3go8&$8*3*3h0k(2)2"
_PRESET_KEY = b"0CoJUm6Qyw8W8jud"
_IV = b"0102030405060708"
_RSA_EXPONENT = 0x010001
_RSA_MODULUS = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f5"
    "6135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97"
    "ddef52741d546b8e289dc6935b3ece0462db0a22b8e7",
    16,
)

_REGISTER = "https://music.163.com/weapi/register/anonimous"
_DYNAMIC_COVER = "https://music.163.com/api/songplay/dynamic-cover"

_guest_state = {"cookie": ""}
_lock = asyncio.Lock()


def _digest(device: str) -> str:
    mixed = "".join(chr(ord(char) ^ ord(_ID_KEY[i % len(_ID_KEY)])) for i, char in enumerate(device))
    return base64.b64encode(hashlib.md5(mixed.encode()).digest()).decode()


def _device() -> str:
    """随机设备号。校验串里带 + 或 / 的会被平台以 code 400 拒绝（约一半），在本地挑掉，不白发请求。"""
    while True:
        device = secrets.token_hex(26).upper()
        if not {"+", "/"} & set(_digest(device)):
            return device


def _username(device: str) -> str:
    """游客注册要的 username：设备号加上它与固定串异或后的 MD5，整体再 Base64。"""
    return base64.b64encode(f"{device} {_digest(device)}".encode()).decode()


def _aes(text: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(_IV)).encryptor()
    return base64.b64encode(encryptor.update(padder.update(text) + padder.finalize()) + encryptor.finalize())


def weapi(payload: dict) -> dict[str, str]:
    """网页端接口的请求体：两次 AES，随机密钥用平台公钥加密后随请求带上。"""
    key = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16))
    params = _aes(_aes(json.dumps(payload).encode(), _PRESET_KEY), key.encode()).decode()
    sealed = pow(int(key[::-1].encode().hex(), 16), _RSA_EXPONENT, _RSA_MODULUS)
    return {"params": params, "encSecKey": format(sealed, "x").zfill(256)}


async def _register(parser: BaseParser) -> str:
    device = _device()
    response = await parser.fetch(
        "POST",
        _REGISTER,
        headers=parser.get_default_headers(),
        data=weapi({"username": _username(device), "csrf_token": ""}),
    )
    value = response.cookies.get("MUSIC_A")
    if response.json().get("code") != 200 or not value:
        raise ValueError("网易云音乐没有发放游客身份")
    return f"MUSIC_A={value}"


async def _guest(parser: BaseParser, stale: str = "") -> str:
    """当前的游客身份；stale 是刚被拒的那个。几个解析同时发现失效时只重领一次。"""
    async with _lock:
        if _guest_state["cookie"] in {"", stale}:
            _guest_state["cookie"] = await _register(parser)
        return _guest_state["cookie"]


async def _ask(parser: BaseParser, song_id: str, cookie: str) -> dict:
    headers = parser.get_default_headers() | {"Cookie": cookie}
    data = await parser.post_json(_DYNAMIC_COVER, headers=headers, data={"songId": song_id})
    if not isinstance(data, dict):
        raise ValueError("网易云音乐没有返回动态封面信息")
    return data


def _video(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (host == "music.126.net" or host.endswith(".music.126.net")):
        raise ValueError("网易云音乐返回的动态封面地址不可用")
    return url


async def dynamic_cover(parser: BaseParser, song_id: str) -> str:
    """歌曲的动态封面视频地址（签名几十分钟后过期）；这首歌没有动态封面时返回空串。"""
    cookie = await _guest(parser)
    data = await _ask(parser, song_id, cookie)
    if data.get("code") == 301:
        data = await _ask(parser, song_id, await _guest(parser, stale=cookie))
    if data.get("code") != 200:
        raise ValueError("网易云音乐暂时没有返回动态封面")
    url = (data.get("data") or {}).get("videoPlayUrl") or ""
    return _video(url) if url else ""
