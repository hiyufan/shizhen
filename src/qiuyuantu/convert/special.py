"""特殊动画在受限子进程中渲染为本地透明帧，再复用现有编码流程。"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path


def _svga(source: Path, directory: Path) -> float:
    from . import svga_data, svga_svg

    try:
        from resvg_py import svg_to_bytes
    except ImportError as exc:
        raise ValueError("SVGA 转换需要安装 qiuyuantu[stickers]") from exc
    movie = svga_data.read(source)
    svga_svg.resolve_shapes(movie)
    images = svga_svg.image_uris(movie)
    count, rate = int(movie["frames"]), float(movie["fps"])
    for index in range(count):
        content = svg_to_bytes(svg_string=svga_svg.frame(movie, index, images), skip_system_fonts=True)
        (directory / f"frame{index:04d}.png").write_bytes(content)
    write_timing(directory, count, rate)
    return count / rate


def write_timing(directory: Path, count: int, rate: float) -> None:
    entries = []
    for index in range(count):
        name = f"frame{index:04d}.png"
        entries.extend((f"file '{name}'", "option framerate 1000", f"duration {1 / rate:.6f}"))
    entries.extend((f"file '{name}'", "option framerate 1000"))
    (directory / "frames.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")


async def render_frames(source: Path, directory: Path, kind: str = "svga", animation: str = "") -> float:
    from .render_process import run

    return await run(kind, source, directory, animation)


def main() -> None:
    if os.name == "posix":
        import resource

        # Chromium 需要较大虚拟地址空间；PAG 由父进程监控整棵进程树的 RSS。
        if sys.argv[1] != "pag":
            resource.setrlimit(resource.RLIMIT_AS, (768 << 20, 768 << 20))
        resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
    try:
        kind = sys.argv[1]
        source, directory = Path(sys.argv[2]), Path(sys.argv[3])
        if kind == "archive":
            with zipfile.ZipFile(source) as archive:
                kind = "svga" if "movie.spec" in archive.namelist() else "spine"
        if kind == "svga":
            duration = _svga(source, directory)
        else:
            from . import pag, spine, vap

            renderers = {"pag": pag.render, "vap": vap.render}
            if kind == "spine":
                count, rate = spine.render(source, directory, sys.argv[4] if len(sys.argv) > 4 else "")
            elif kind in renderers:
                count, rate = renderers[kind](source, directory)
            else:
                raise ValueError("不支持的动画格式")
            write_timing(directory, count, rate)
            duration = count / rate
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    except Exception:
        print(json.dumps({"error": "动画文件无效或渲染失败"}))
        sys.exit(1)
    print(json.dumps({"duration": duration}))


if __name__ == "__main__":
    main()
