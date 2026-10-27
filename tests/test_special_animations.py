"""独立动画格式的还原、资源边界与子进程回收。"""

import asyncio
import io
import json
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from PIL import Image

from qiuyuantu import protobuf
from qiuyuantu.convert import render_process, spine, spine_data, spine_pose, svga_data, svga_svg, vap


def vint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result) + bytes([value])


def field(number, value):
    if isinstance(value, bytes):
        return vint(number * 8 + 2) + vint(len(value)) + value
    if isinstance(value, float):
        return vint(number * 8 + 5) + struct.pack("<f", value)
    return vint(number * 8) + vint(value)


def png(size=(8, 8), color=(255, 0, 0, 255)):
    stream = io.BytesIO()
    Image.new("RGBA", size, color).save(stream, format="PNG")
    return stream.getvalue()


def svga_file(tmp_path):
    params = field(1, 16.0) + field(2, 16.0) + field(3, 10) + field(4, 2)
    image = field(1, b"picture") + field(2, png())
    layout = field(3, 8.0) + field(4, 8.0)
    frames = []
    for position in (0.0, 8.0):
        matrix = field(1, 1.0) + field(4, 1.0) + field(5, position)
        frames.append(field(1, 1.0) + field(2, layout) + field(3, matrix))
    sprite = field(1, b"picture") + b"".join(field(2, frame) for frame in frames)
    output = tmp_path / "test.svga"
    output.write_bytes(zlib.compress(field(2, params) + field(3, image) + field(4, sprite)))
    return output


def test_svga_real_frame_positions_and_transparency(tmp_path):
    resvg = pytest.importorskip("resvg_py")
    movie = svga_data.read(svga_file(tmp_path))
    images = svga_svg.image_uris(movie)
    frames = [
        Image.open(io.BytesIO(resvg.svg_to_bytes(svg_string=svga_svg.frame(movie, index, images)))) for index in (0, 1)
    ]
    assert frames[0].getpixel((2, 2)) == (255, 0, 0, 255)
    assert frames[0].getpixel((10, 2))[3] == 0
    assert frames[1].getpixel((2, 2))[3] == 0
    assert frames[1].getpixel((10, 2)) == (255, 0, 0, 255)


def test_svga_zero_transform_components_are_not_replaced_by_identity():
    matrix = field(2, 1.0) + field(3, -1.0)
    frame = svga_data._frame(field(3, matrix))
    assert frame["transform"]["a"] == 0 and frame["transform"]["d"] == 0
    assert svga_svg._matrix(frame["transform"]) == "matrix(0 1.0 -1.0 0 0 0)"
    shape = svga_data._shape(field(11, matrix))
    assert svga_svg._matrix(shape["transform"]) == "matrix(0 1.0 -1.0 0 0 0)"


def test_svga_v1_zip_vector_colors_and_stroke_names(tmp_path):
    resvg = pytest.importorskip("resvg_py")
    shape = {
        "type": "rect",
        "args": {"width": 8, "height": 8},
        "styles": {"fill": [1, 0, 0, 1], "lineCap": "round", "lineJoin": "bevel", "lineDash": [2, 3]},
    }
    spec = {
        "movie": {"viewBox": {"width": 16, "height": 16}, "fps": 10, "frames": 2},
        "sprites": [{"frames": [{"alpha": 1, "shapes": [shape]}, {"alpha": 1, "shapes": [{"type": "keep"}]}]}],
    }
    source = tmp_path / "vector.svga"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("movie.spec", json.dumps(spec))
    movie = svga_data.read(source)
    svga_svg.resolve_shapes(movie)
    for index in (0, 1):
        svg = svga_svg.frame(movie, index, {})
        assert 'stroke-linecap="round"' in svg and 'stroke-linejoin="bevel"' in svg
        assert 'stroke-dasharray="2.0 3.0"' in svg
        image = Image.open(io.BytesIO(resvg.svg_to_bytes(svg_string=svg)))
        assert image.getpixel((2, 2)) == (255, 0, 0, 255)
        assert image.getpixel((12, 12))[3] == 0


def test_svga_bounded_decompression_and_truncation_fail(tmp_path, monkeypatch):
    source = svga_file(tmp_path)
    monkeypatch.setattr(svga_data, "MAX_BYTES", 8)
    with pytest.raises(ValueError, match="超过限制"):
        svga_data.read(source)
    source.write_bytes(source.read_bytes()[:-3])
    with pytest.raises(ValueError):
        svga_data.read(source)


def test_svga_matte_uses_alpha_instead_of_luminance():
    resvg = pytest.importorskip("resvg_py")
    movie = {
        "width": 8,
        "height": 8,
        "images": {"color": png(), "mask": png(color=(0, 0, 0, 128))},
        "sprites": [
            {"imageKey": "mask", "frames": [{"alpha": 1, "layout": {"width": 8, "height": 8}}]},
            {"imageKey": "color", "matteKey": "mask", "frames": [{"alpha": 1, "layout": {"width": 8, "height": 8}}]},
        ],
    }
    image = Image.open(io.BytesIO(resvg.svg_to_bytes(svg_string=svga_svg.frame(movie, 0, svga_svg.image_uris(movie)))))
    assert image.getpixel((2, 2)) == (255, 0, 0, 128)


@pytest.mark.parametrize("data", [b"\x00", b"\x0a\x04x", b"\x08\x80", b"\x0f", b"\x80" * 11])
def test_invalid_protobuf_is_rejected(data):
    with pytest.raises(ValueError):
        protobuf.fields(data)


def test_vap_metadata_is_read_from_box_and_checked(tmp_path):
    info = {
        "w": 16,
        "h": 16,
        "f": 20,
        "fps": 10,
        "videoW": 32,
        "videoH": 16,
        "aFrame": [16, 0, 16, 16],
        "rgbFrame": [0, 0, 16, 16],
    }
    raw = json.dumps({"info": info}).encode()
    path = tmp_path / "video.mp4"
    path.write_bytes(struct.pack(">I4s", len(raw) + 8, b"vapc") + raw)
    assert vap.validate(vap.metadata(path)) == info
    with pytest.raises(ValueError, match="额外融合"):
        vap.validate({"info": info, "src": [{"srcTag": "remote"}]})
    with pytest.raises(ValueError, match="超过视频尺寸"):
        vap.validate({"info": info | {"aFrame": [30, 0, 16, 16]}})
    with pytest.raises(ValueError):
        vap.validate({"info": info | {"fps": float("nan")}})


def test_vap_mp4_input_size_is_limited_before_decoding(tmp_path):
    source = tmp_path / "large.mp4"
    with source.open("wb") as stream:
        stream.truncate((32 << 20) + 1)
    with pytest.raises(ValueError, match="32 MiB"):
        vap.render(source, tmp_path)


def spine_zip(tmp_path, *, attachment=None, extra=None):
    data = {
        "skeleton": {"spine": "4.2.22"},
        "bones": [{"name": "root"}],
        "slots": [{"name": "slot", "bone": "root", "attachment": "tile"}],
        "skins": [{"name": "default", "attachments": {"slot": {"tile": attachment or {"width": 8, "height": 8}}}}],
        "animations": {"move": {"bones": {"root": {"translate": [{"x": 0}, {"time": 0.1, "x": 8}]}}}},
    }
    if extra:
        data.update(extra)
    path = tmp_path / "spine.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("folder/character.json", json.dumps(data))
        archive.writestr("folder/page.atlas", "page.png\n\tsize: 8,8\ntile\n\tbounds: 0,0,8,8\n")
        archive.writestr("folder/page.png", png())
    return path


def test_spine_archive_lists_real_animations_and_renders_moving_regions(tmp_path):
    pytest.importorskip("resvg_py")
    path = spine_zip(tmp_path)
    assert spine_data.describe(path) == ["move"]
    output = tmp_path / "frames"
    output.mkdir()
    count, rate = spine.render(path, output, "move")
    assert (count, rate) == (3, 30)
    first = Image.open(output / "frame0000.png")
    last = Image.open(output / "frame0002.png")
    assert first.getbbox()[0] < last.getbbox()[0]
    assert first.getextrema()[3][0] == 0


@pytest.mark.parametrize("feature", ["mesh", "ik", "physics"])
def test_spine_complex_features_fail_instead_of_losing_layers(tmp_path, feature):
    pytest.importorskip("resvg_py")
    path = spine_zip(
        tmp_path,
        attachment={"type": "mesh"} if feature == "mesh" else None,
        extra={feature: [{"name": "constraint"}]} if feature != "mesh" else None,
    )
    output = tmp_path / "frames"
    output.mkdir()
    with pytest.raises(ValueError, match="尚不支持|暂时支持"):
        spine.render(path, output)
    assert not list(output.iterdir())


def test_spine_zip_rejects_traversal_and_missing_atlas(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../outside.png", png())
    with pytest.raises(ValueError, match="路径"):
        spine_data.archive(path)


def test_spine_bezier_uses_absolute_time_and_values_in_v4():
    row = {"time": 0.5, "x": 10, "curve": [0.7, 10, 0.8, 20]}
    following = {"time": 1, "x": 20}
    assert spine_pose._curve(row, following, "x", 0, 0.75, True, 0) == pytest.approx(15, abs=0.01)
    assert spine_pose.sample([{"time": 1, "value": 10}], ("value",), 0, True) is None


async def test_cancelled_renderer_is_killed_and_waited(monkeypatch):
    class Process:
        pid = 76543210
        waited = False

        def __init__(self):
            self.exited = asyncio.Event()

        async def communicate(self):
            await self.exited.wait()
            return b"", b""

        async def wait(self):
            self.waited = True

    process = Process()
    killed = []

    def kill(child):
        killed.append(child.pid)
        child.exited.set()

    monkeypatch.setattr(render_process, "_kill", kill)
    task = asyncio.create_task(render_process._communicate(process))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed == [process.pid] and process.waited


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="Linux process tree accounting")
async def test_renderer_reclaims_detached_browser_descendants():
    import os
    import sys

    code = (
        'import subprocess,sys,time; child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
        "start_new_session=True); print(child.pid,flush=True); time.sleep(60)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE, start_new_session=True
    )
    try:
        child = int(await asyncio.wait_for(process.stdout.readline(), 5))
        assert os.getpgid(child) == child
        assert child in render_process._process_tree(process.pid)
        assert render_process._resident_bytes(process.pid) > 0
        render_process._kill(process)
        await asyncio.wait_for(process.wait(), 5)
        state = Path("/proc") / str(child) / "stat"
        assert not state.exists() or state.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    finally:
        if process.returncode is None:
            render_process._kill(process)
            await process.wait()


async def test_pag_browser_preparation_has_its_own_concurrency_limit(monkeypatch, tmp_path):
    active = peak = 0

    async def render(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return 1.0

    monkeypatch.setattr(render_process, "_run", render)
    render_process._browser_slot.cache_clear()
    await asyncio.gather(*(render_process.run("pag", tmp_path / "a.pag", tmp_path) for _ in range(3)))
    assert peak == 1
    render_process._browser_slot.cache_clear()


def test_bundled_pag_files_match_the_pinned_manifest():
    import hashlib

    root = Path(__file__).resolve().parents[1] / "src/qiuyuantu/convert/vendor"
    manifest = json.loads((root / "LIBPAG-MANIFEST.json").read_text())
    assert manifest["version"] == "4.5.85"
    for name, digest in manifest["files_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest


def test_svga_keep_shape_preserves_previous_vector():
    movie = {
        "sprites": [
            {
                "frames": [
                    {"shapes": [{"type": 1, "args": {"width": 8}}, {"type": 2, "args": {"radiusX": 4}}]},
                    {"shapes": [{"type": 3}]},
                ]
            }
        ]
    }
    svga_svg.resolve_shapes(movie)
    assert movie["sprites"][0]["frames"][1]["shapes"] == movie["sprites"][0]["frames"][0]["shapes"]
