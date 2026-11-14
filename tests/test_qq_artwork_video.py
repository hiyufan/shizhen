"""真实编码回归：QQ 原片的 B 帧时序，以及方形版的中心裁切。"""

import shutil

import imageio_ffmpeg
import pytest
from PIL import Image, ImageDraw

from qiuyuantu.convert import artwork, ffmpeg


@pytest.fixture
async def cover_video(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", imageio_ffmpeg.get_ffmpeg_exe)
    for index in range(16):
        frame = Image.new("RGB", (64, 96), "black")
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 16, 63, 79), fill=(50 + index * 8, 90, 200 - index * 5))
        draw.rectangle((index * 3, 40, index * 3 + 4, 55), fill="white")
        frame.save(tmp_path / f"frame-{index:02d}.png")
    source = tmp_path / "input.mp4"
    await ffmpeg.run(
        [
            "-framerate",
            "25",
            "-i",
            str(tmp_path / "frame-%02d.png"),
            "-c:v",
            "libx264",
            "-x264-params",
            "b-adapt=0:bframes=3",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    return source


async def _frames(path):
    checksum = path.with_suffix(".md5")
    await ffmpeg.run(
        [
            "-copyts",
            "-i",
            str(path),
            "-fps_mode",
            "passthrough",
            "-f",
            "framemd5",
            str(checksum),
        ]
    )
    rows = [row.split(",") for row in checksum.read_text().splitlines() if not row.startswith("#")]
    return [(int(row[2]), row[5].strip()) for row in rows]


@pytest.mark.parametrize("square", [False, True])
async def test_qq_download_keeps_first_frame_at_zero_and_all_frames(cover_video, tmp_path, monkeypatch, square):
    async def source(_page, directory, _report):
        target = directory / "source.mp4"
        shutil.copyfile(cover_video, target)
        return target

    monkeypatch.setattr(artwork, "_qq_source", source)
    spec = "qqmusic-artwork:square" if square else "qqmusic-artwork"
    output = await artwork.download(
        "https://y.qq.com/n/ryqq_v2/albumDetail/42", spec, tmp_path, "output", lambda *_: None
    )
    original, result = await _frames(cover_video), await _frames(output)
    assert len(original) == len(result) == 16
    assert [pts for pts, _ in result] == [pts for pts, _ in original] == list(range(16))
    info = await ffmpeg.probe(output)
    assert (info.width, info.height) == ((64, 64) if square else (64, 96))
    assert info.fps == 25 and info.duration == pytest.approx(0.64)
    if not square:
        assert result == original
    else:
        # avcC 的 AVCProfileIndication：66 是 Baseline，兼容不支持 B 帧的解码器。
        encoded = output.read_bytes()
        assert encoded[encoded.index(b"avcC") + 5] == 66
    image = tmp_path / "output.png"
    await ffmpeg.run(["-i", str(output), "-frames:v", "1", str(image)])
    with Image.open(image) as frame:
        top, bottom = frame.getpixel((32, 0)), frame.getpixel((32, frame.height - 1))
        assert (min(top) > 25 and min(bottom) > 25) if square else (max(top) < 10 and max(bottom) < 10)


async def test_default_remux_retains_the_existing_timestamp_policy(cover_video, tmp_path):
    output = tmp_path / "legacy.mp4"
    await artwork._remux(cover_video, output)
    original, result = await _frames(cover_video), await _frames(output)
    assert result[0][0] > original[0][0]
    assert [checksum for _, checksum in result] == [checksum for _, checksum in original]
