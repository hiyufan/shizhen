"""Signal 公开包的认证解密与临时素材引用；包密钥不放入下载 URL。"""

from __future__ import annotations

import asyncio
import io
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from PIL import Image

from ..utils import create_async_client

_PREFIX = "https://signal.art/qiuyuantu-assets/"
_REFERENCES: dict[str, tuple[float, str, bytes, int]] = {}
_TTL = 3 * 3600
_MAX_REFS = 5000
_CDN = "https://cdn-ca.signal.org/stickers/"


def public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname == "signal.art":
        pack = parse_qs(parsed.fragment).get("pack_id", [""])[0]
        return (
            "https://signal.art/addstickers/#pack_id=" + pack
            if re.fullmatch(r"[0-9a-f]{32}", pack)
            else "https://signal.art/addstickers/"
        )
    return url


def reference(url: str) -> tuple[str, bytes]:
    parsed = urlparse(url)
    params = parse_qs(parsed.fragment)
    pack, key = (params.get(name, [""])[0] for name in ("pack_id", "pack_key"))
    if parsed.scheme != "https" or parsed.hostname != "signal.art" or parsed.path.rstrip("/") != "/addstickers":
        raise ValueError("请粘贴 Signal 完整的公开贴纸包分享链接")
    if not re.fullmatch(r"[0-9a-f]{32}", pack) or not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Signal 分享链接不完整，请保留 # 后面的包参数")
    return pack, bytes.fromhex(key)


def decrypt(data: bytes, key: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives import hashes, hmac, padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as exc:
        raise ValueError("Signal 贴纸需要安装 cryptography") from exc
    if len(data) < 64 or (len(data) - 48) % 16:
        raise ValueError("Signal 加密文件长度无效")
    derived = HKDF(algorithm=hashes.SHA256(), length=64, salt=bytes(32), info=b"Sticker Pack").derive(key)
    try:
        mac = hmac.HMAC(derived[32:], hashes.SHA256())
        mac.update(data[:-32])
        mac.verify(data[-32:])
    except Exception as exc:
        raise ValueError("Signal 文件认证失败，请检查分享链接是否完整") from exc
    decoder = Cipher(algorithms.AES(derived[:32]), modes.CBC(data[:16])).decryptor()
    padded = decoder.update(data[16:-32]) + decoder.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return unpad.update(padded) + unpad.finalize()


async def fetch_encrypted(pack: str, suffix: str, limit: int) -> bytes:
    async with (
        create_async_client(timeout=20, follow_redirects=True) as client,
        client.stream("GET", _CDN + pack + "/" + suffix) as response,
    ):
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes(65536):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("Signal 文件超过大小限制")
    return bytes(data)


def sweep() -> None:
    now = time.time()
    for token, item in list(_REFERENCES.items()):
        if item[0] <= now:
            _REFERENCES.pop(token, None)


def available(url: str) -> bool:
    if not is_asset_url(url):
        return False
    item = _REFERENCES.get(url[len(_PREFIX) : -5])
    return bool(item and item[0] > time.time())


def register(pack: str, key: bytes, sticker: int) -> str:
    now = time.time()
    sweep()
    while len(_REFERENCES) >= _MAX_REFS:
        _REFERENCES.pop(next(iter(_REFERENCES)))
    token = secrets.token_hex(16)
    _REFERENCES[token] = (now + _TTL, pack, key, sticker)
    return _PREFIX + token + ".webp"


def is_asset_url(url: str) -> bool:
    return url.startswith(_PREFIX) and bool(re.fullmatch(r"[a-f0-9]{32}\.webp", url[len(_PREFIX) :]))


@dataclass(frozen=True)
class Sticker:
    content: bytes
    media_type: str
    ext: str
    animated: bool


def _sticker(data: bytes) -> Sticker:
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"PNG", "WEBP", "GIF"} or max(image.size) > 1024:
            raise ValueError("Signal 贴纸图片格式或尺寸无效")
        extension = {"PNG": "png", "WEBP": "webp", "GIF": "gif"}[image.format]
        animated = getattr(image, "n_frames", 1) > 1
        image.verify()
    return Sticker(data, "image/" + extension, extension, animated)


async def read_asset(url: str) -> Sticker:
    if not is_asset_url(url):
        raise ValueError("Signal 素材引用无效")
    token = url[len(_PREFIX) : -5]
    item = _REFERENCES.get(token)
    if not item or item[0] <= time.time():
        _REFERENCES.pop(token, None)
        raise ValueError("贴纸已过期，请重新粘贴完整分享链接解析")
    _, pack, key, sticker = item
    raw = await fetch_encrypted(pack, "full/" + str(sticker), 4 << 20)
    return await asyncio.to_thread(_sticker, decrypt(raw, key))
