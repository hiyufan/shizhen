"""有界 TGS / Lottie JSON 读取与隔离渲染；原生解码器不进入 Web 进程。"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import math
import os
import sys
from pathlib import Path


def animation_data(source: Path) -> dict:
    content = _animation_bytes(source)
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("贴纸动画内容无效")
    _check_resources(data)
    width, height, rate = (float(data.get(key, 0)) for key in ("w", "h", "fr"))
    count = float(data.get("op", 0)) - float(data.get("ip", 0))
    if not all(math.isfinite(value) and value > 0 for value in (width, height, rate, count)):
        raise ValueError("贴纸动画尺寸或帧率无效")
    if width > 512 or height > 512 or rate > 60 or count > 600 or count / rate > 10:
        raise ValueError("贴纸动画超过尺寸、帧数或时长限制")
    if width * height * count > 100_000_000:
        raise ValueError("贴纸动画超过总像素限制")
    return data


def _animation_bytes(source: Path) -> bytes:
    with source.open("rb") as stream:
        compressed = stream.read(2) == b"\x1f\x8b"
    if not compressed:
        if source.stat().st_size > 1_000_000:
            raise ValueError("Lottie JSON 文件超过 1 MB，暂不支持转换")
        return source.read_bytes()
    if source.stat().st_size > 256_000:
        raise ValueError("TGS 文件超过 256 KB，暂不支持转换")
    with gzip.GzipFile(fileobj=io.BytesIO(source.read_bytes())) as zipped:
        content = zipped.read(4_000_001)
    if len(content) > 4_000_000:
        raise ValueError("TGS 解压后的内容过大")
    return content


def _check_resources(value) -> None:
    if isinstance(value, dict):
        if value.get("fonts") or value.get("chars") or isinstance(value.get("p"), str) or value.get("u"):
            raise ValueError("贴纸动画包含外部图片或字体资源，无法转换")
        for item in value.values():
            _check_resources(item)
    elif isinstance(value, list):
        for item in value:
            _check_resources(item)


def _render(source: Path, directory: Path) -> float:
    data = animation_data(source)
    try:
        from rlottie_python import LottieAnimation
    except ImportError as exc:
        raise ValueError("贴纸动画转换需要安装 qiuyuantu[stickers]") from exc
    rate = float(data["fr"])
    with LottieAnimation.from_data(json.dumps(data), resource_path="") as animation:
        rendered = animation.lottie_animation_get_totalframe()
        expected = float(data["op"]) - float(data.get("ip", 0))
        count = math.ceil(expected)
        if not 2 <= count <= 600 or abs(rendered - expected) > 1:
            raise ValueError("贴纸动画渲染器返回的帧数无效")
        entries = []
        for index in range(count):
            name = f"frame{index:04d}.png"
            animation.render_pillow_frame(index).save(directory / name)
            entries.extend((f"file '{name}'", "option framerate 1000", f"duration {1 / rate:.6f}"))
        entries.extend((f"file '{name}'", "option framerate 1000"))
        (directory / "frames.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
    return count / rate


async def render_frames(source: Path, directory: Path) -> float:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "qiuyuantu.convert.tgs",
        str(source),
        str(directory),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 30)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    try:
        result = json.loads(output)
    except ValueError as exc:
        raise ValueError("贴纸动画渲染失败，请检查文件或转换依赖") from exc
    if process.returncode:
        raise ValueError(result.get("error") or "贴纸动画渲染失败")
    return float(result["duration"])


def main() -> None:
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 << 20, 768 << 20))
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    try:
        duration = _render(Path(sys.argv[1]), Path(sys.argv[2]))
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    except Exception:
        print(json.dumps({"error": "贴纸动画文件无效或渲染失败"}))
        sys.exit(1)
    print(json.dumps({"duration": duration}))


if __name__ == "__main__":
    main()
