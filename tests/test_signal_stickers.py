"""公开 Signal 协议、先认证后解密、临时引用与密钥日志隔离。"""

import io
import logging
from urllib.parse import quote

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, hmac, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from PIL import Image

from qiuyuantu import parser
from qiuyuantu.convert import signal
from qiuyuantu.web.access_log import ShareLinkFilter
from qiuyuantu.web.parse import ParseCache, _client_data, _handle_failure

KEY = bytes(range(32))
PACK = "a" * 32
LINK = "https://signal.art/addstickers/#pack_id=" + PACK + "&pack_key=" + KEY.hex()


def encrypt(data):
    derived = HKDF(algorithm=hashes.SHA256(), length=64, salt=bytes(32), info=b"Sticker Pack").derive(KEY)
    pad = padding.PKCS7(128).padder()
    padded = pad.update(data) + pad.finalize()
    encoder = Cipher(algorithms.AES(derived[:32]), modes.CBC(bytes(16))).encryptor()
    raw = bytes(16) + encoder.update(padded) + encoder.finalize()
    mac = hmac.HMAC(derived[32:], hashes.SHA256())
    mac.update(raw)
    return raw + mac.finalize()


@pytest.fixture
def signal_http(monkeypatch):
    image = io.BytesIO()
    Image.new("RGBA", (16, 16), (255, 0, 0, 128)).save(image, format="WEBP", lossless=True)
    state = {"calls": [], "media": image.getvalue()}
    # 标题、作者、贴纸 id=7；不依赖运行时生成的 protobuf 类。
    manifest = b"\x0a\x04Test\x12\x06Artist\x22\x02\x08\x07"

    def handle(request):
        state["calls"].append(request)
        data = manifest if request.url.path.endswith("manifest.proto") else state["media"]
        return httpx.Response(200, content=encrypt(data))

    monkeypatch.setattr(
        signal, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    signal._REFERENCES.clear()
    return state


async def test_public_pack_references_never_return_key_and_fetch_is_lazy(signal_http):
    info = await parser.parse_video_share_url(LINK)
    assert info.title == "Test" and len(info.assets) == 1 and len(signal_http["calls"]) == 1
    data = _client_data(info, LINK)
    assert KEY.hex() not in str(data) and "pack_key" not in str(data)
    sticker = await signal.read_asset(info.assets[0].url)
    assert sticker.media_type == "image/webp" and sticker.content == signal_http["media"]
    assert not sticker.animated
    assert len(signal_http["calls"]) == 2
    assert all(request.url.host == "cdn-ca.signal.org" for request in signal_http["calls"])
    assert all("authorization" not in r.headers and "cookie" not in r.headers for r in signal_http["calls"])


def test_tampered_ciphertext_cannot_pass_authentication():
    raw = bytearray(encrypt(b"public sticker"))
    raw[18] ^= 1
    with pytest.raises(ValueError, match="认证失败"):
        signal.decrypt(bytes(raw), KEY)
    with pytest.raises(ValueError, match="长度无效"):
        signal.decrypt(b"short", KEY)


async def test_expired_reference_invalidates_parse_cache(signal_http, monkeypatch):
    info = await parser.parse_video_share_url(LINK)
    cache = ParseCache()
    cache.put(LINK, {"code": 200, "data": _client_data(info, LINK)})
    assert cache.get(LINK)
    monkeypatch.setattr(signal.time, "time", lambda: 10**12)
    assert cache.get(LINK) is None
    with pytest.raises(ValueError, match="已过期"):
        await signal.read_asset(info.assets[0].url)


async def test_failure_records_do_not_include_share_key(monkeypatch, caplog):
    records = []
    monkeypatch.setattr("qiuyuantu.web.parse.feedback.make_ticket", lambda url, *args: records.append(url))
    monkeypatch.setattr("qiuyuantu.web.parse.stats.counted", lambda ip: False)
    await _handle_failure({"reason": "network", "msg": "失败"}, LINK, "signal", "test")
    assert KEY.hex() not in caplog.text and all(KEY.hex() not in item for item in records)


@pytest.mark.parametrize("levels", [0, 1, 2])
def test_legacy_get_access_log_removes_pack_key(levels):
    target = LINK
    for _ in range(levels):
        target = quote(target, safe="")
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s %s %s",
        ("client", "GET", "/api/parse?url=" + target, "1.1", 200),
        None,
    )
    assert ShareLinkFilter().filter(record)
    assert "pack_key" not in record.getMessage() and KEY.hex() not in record.getMessage()


@pytest.mark.parametrize(
    "url",
    [
        "https://signal.art/addstickers/#pack_id=" + PACK,
        LINK.replace("signal.art", "signal.art.evil.example"),
        LINK.replace(KEY.hex(), "bad"),
    ],
)
def test_incomplete_or_wrong_host_link_is_rejected(url):
    with pytest.raises(ValueError):
        signal.reference(url)
