"""壁纸包与纹理真实二进制解码、受限进程及签名下载工作流。"""

import importlib
import io
import json
import struct
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from qiuyuantu import web
from qiuyuantu.convert import jobs, net, wallpaper, wallpaper_tex, wallpaper_uploads

app_module = importlib.import_module("qiuyuantu.web.app")


def integers(*values):
    return struct.pack("<" + "I" * len(values), *values)


def pkg(files):
    magic = b"PKGV0001"
    header = integers(len(magic)) + magic + integers(len(files))
    offset = 0
    body = b""
    for name, content in files:
        encoded = name.encode()
        header += integers(len(encoded)) + encoded + integers(offset, len(content))
        offset += len(content)
        body += content
    return header + body


def tex(content, fmt=0, width=4, height=4, version=1, compressed=False, image_format=0xFFFFFFFF, display=None):
    dw, dh = display or (width, height)
    header = b"TEXV0005\0TEXI0001\0" + integers(fmt, 0, width, height, dw, dh, 0)
    header += f"TEXB{version:04d}".encode() + b"\0" + integers(1)
    if version >= 3:
        header += integers(image_format)
    header += integers(1, width, height)
    size = len(content)
    if compressed:
        content = b"\xf0" + bytes([size - 15]) + content
    if version >= 2:
        header += integers(int(compressed), size)
    return header + integers(len(content)) + content


def png():
    output = io.BytesIO()
    Image.new("RGBA", (3, 2), (12, 34, 56, 128)).save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_raw_texture_crops_padding_and_preserves_alpha(version):
    content = tex(bytes([255, 0, 0, 127]) * 16, version=version, display=(3, 2))
    data, extension = wallpaper_tex.decode(content)
    image = Image.open(io.BytesIO(data))
    assert extension == ".png" and image.size == (3, 2) and image.getpixel((0, 0)) == (255, 0, 0, 127)


@pytest.mark.parametrize("fmt", [4, 6, 7])
def test_dxt_pixel_blocks_decode_with_correct_colors(fmt):
    colors = bytes.fromhex("00f8000000000000")
    alpha = {4: bytes.fromhex("ffff000000000000"), 6: b"\xff" * 8, 7: b""}[fmt]
    data, _ = wallpaper_tex.decode(tex(alpha + colors, fmt=fmt))
    assert Image.open(io.BytesIO(data)).getpixel((0, 0)) == (255, 0, 0, 255)


def test_lz4_texture_and_overlapping_back_reference():
    data, _ = wallpaper_tex.decode(tex(bytes([2, 4, 6, 8]) * 16, version=2, compressed=True))
    assert Image.open(io.BytesIO(data)).getpixel((1, 1)) == (2, 4, 6, 8)
    assert wallpaper_tex.decompress(b"\x13a\x01\x00", 8) == b"a" * 8


@pytest.mark.parametrize("data,size", [(b"\x00\x01\x00", 4), (b"\xf0", 100), (b"\x20ab", 1), (b"\x10a", 2)])
def test_lz4_invalid_references_truncation_and_output_size_are_rejected(data, size):
    with pytest.raises(ValueError):
        wallpaper_tex.decompress(data, size)


def test_embedded_png_decodes_and_oversized_texture_is_rejected():
    data, _ = wallpaper_tex.decode(tex(png(), width=3, height=2, version=3, image_format=13))
    assert Image.open(io.BytesIO(data)).getpixel((0, 0)) == (12, 34, 56, 128)
    with pytest.raises(ValueError, match="尺寸"):
        wallpaper_tex.decode(tex(b"", width=65535, height=65535))


def test_embedded_video_texture_uses_its_mp4_payload():
    payload = b"\0\0\0\x18ftypisom\0\0\0\0isommp42"
    header = b"TEXV0005\0TEXI0001\0" + integers(0, 32, 4, 4, 4, 4, 0)
    header += b"TEXB0004\0" + integers(1, 0xFFFFFFFF, 1, 1, 1, 2) + b"{}\0"
    header += integers(1, 4, 4, 0, len(payload), len(payload))
    assert wallpaper_tex.decode(header + payload) == (payload, ".mp4")


@pytest.mark.parametrize("name", ["../escape.png", "/absolute.png", "C:\\file.png", "sub/../../a.png"])
def test_package_paths_cannot_escape(name):
    with pytest.raises(ValueError, match="路径"):
        wallpaper.entries(pkg([(name, png())]))


def test_package_rejects_duplicate_names_truncation_and_overlapping_ranges():
    with pytest.raises(ValueError):
        wallpaper.entries(pkg([("a.png", png()), ("a.png", png())]))
    with pytest.raises(ValueError):
        wallpaper.entries(pkg([("a.png", png())])[:-1])
    data = bytearray(pkg([("a.png", png()), ("b.png", png())]))
    second_offset = 4 + 8 + 4 + (4 + 5 + 8) + 4 + 5
    data[second_offset : second_offset + 4] = integers(0)
    with pytest.raises(ValueError, match="重叠"):
        wallpaper.entries(bytes(data))


def test_extraction_ignores_scripts_reports_unsupported_textures_and_preserves_image_bytes(tmp_path):
    source = tmp_path / "source.pkg"
    source.write_bytes(
        pkg(
            [
                ("scripts/run.js", b"throw 1"),
                ("materials/background.tex", tex(bytes([1, 2, 3, 255]) * 16)),
                ("images/original.png", png()),
                ("unknown.tex", b"bad"),
            ]
        )
    )
    directory = tmp_path / "output"
    directory.mkdir()
    wallpaper.extract(source, directory, ".pkg")
    manifest = json.loads((directory / "manifest.json").read_text())
    assert [item["name"] for item in manifest["assets"]] == ["background.png", "original.png"]
    assert len(manifest["skipped"]) == 1
    assert (directory / "1.png").read_bytes() == png()
    with zipfile.ZipFile(directory / "assets.zip") as archive:
        assert archive.namelist() == ["1_background.png", "2_original.png"]


@pytest.fixture
def client(monkeypatch):
    # 只启动被测任务，不启动真实平台探测、浏览器预热和统计同步。
    monkeypatch.setattr(app_module, "_background_jobs", lambda: [])
    jobs._semaphore.cache_clear()
    with TestClient(web.app) as client:
        yield client


def completed(client, response):
    assert response.status_code == 200
    initial = response.json()
    assert initial["wallpaper"] is True
    for _ in range(100):
        job = client.get("/api/jobs/" + initial["job"]["id"]).json()
        if job["status"] in {"done", "error"}:
            return job
        time.sleep(0.02)
    pytest.fail("wallpaper worker did not finish")


def test_upload_worker_preview_download_zip_signatures_and_expiration(client):
    response = client.post(
        "/api/upload", files={"file": ("local.pkg", pkg([("art.png", png())]), "application/octet-stream")}
    )
    job = completed(client, response)
    assert job["status"] == "done", job
    data = job["extra"]
    assert "file" not in data["assets"][0] and "path" not in json.dumps(data)
    ident, item = data["wallpaper_upload"], data["assets"][0]
    path = f"/api/wallpaper/{ident}/0"
    assert client.get(path).status_code == 403
    preview = client.get(path, params={"sig": item["sig"]})
    assert preview.content == png() and preview.headers["content-type"] == "image/png"
    assert preview.headers["content-security-policy"].startswith("sandbox")
    download = client.get(path, params={"sig": item["sig"], "download": 1})
    assert download.headers["content-disposition"].startswith("attachment")
    assert client.get(f"/api/wallpaper/{ident}/all", params={"sig": item["sig"]}).status_code == 403
    bundle = client.get(f"/api/wallpaper/{ident}/all", params={"sig": data["sig"]})
    assert zipfile.ZipFile(io.BytesIO(bundle.content)).read("1_art.png") == png()
    assert (
        client.post("/api/wallpaper/prepare", json={"upload_id": ident, "asset_id": "0", "sig": "bad"}).status_code
        == 403
    )
    upload = wallpaper_uploads.get(ident)
    assert upload.directory.resolve() in wallpaper_uploads.known_paths()
    upload.expires = 0
    wallpaper_uploads.sweep()
    assert not upload.directory.exists() and client.get(path, params={"sig": item["sig"]}).status_code == 410


def test_invalid_upload_fails_in_worker_and_removes_files(client):
    before = wallpaper_uploads.known_paths()
    response = client.post(
        "/api/upload", files={"file": ("broken.tex", b"invalid texture", "application/octet-stream")}
    )
    job = completed(client, response)
    assert job["status"] == "error" and "不完整" in job["error"]
    assert wallpaper_uploads.known_paths() == before


def test_standalone_tex_upload_exposes_a_decoded_png(client):
    texture = tex(bytes([20, 40, 60, 80]) * 16)
    response = client.post("/api/upload", files={"file": ("texture.tex", texture, "application/octet-stream")})
    job = completed(client, response)
    assert job["status"] == "done", job
    data = job["extra"]
    item = data["assets"][0]
    image = client.get(f"/api/wallpaper/{data['wallpaper_upload']}/0", params={"sig": item["sig"]})
    assert Image.open(io.BytesIO(image.content)).getpixel((0, 0)) == (20, 40, 60, 80)


def test_wallpaper_prepare_validates_asset_reference(client):
    ident = "a" * 32
    token = net.sign(wallpaper_uploads.signature(ident, "0"))
    response = client.post("/api/wallpaper/prepare", json={"upload_id": ident, "asset_id": "0", "sig": token})
    assert response.status_code == 410


def test_extraction_respects_reserved_output_budget(tmp_path):
    source = tmp_path / "source.pkg"
    source.write_bytes(pkg([("art.png", png())]))
    with pytest.raises(ValueError, match="体积限制"):
        wallpaper.extract(source, tmp_path, ".pkg", budget=1)


def test_register_checks_shared_disk_quota_before_starting_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(wallpaper_uploads.store, "disk_usage", lambda: wallpaper_uploads.config.DISK_QUOTA_BYTES)
    source = tmp_path / "source.pkg"
    source.write_bytes(b"package")
    with pytest.raises(ValueError, match="空间不足"):
        wallpaper_uploads.register(source, "wallpaper", ".pkg")
