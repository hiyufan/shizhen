"""特殊渲染进程组的墙钟、内存预算与取消回收。"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import signal
import sys
from pathlib import Path


def _process_tree(root: int) -> dict[int, int]:
    if os.name != "posix" or not Path("/proc").is_dir():
        return {}
    processes = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            values = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            processes[int(entry.name)] = (int(values[1]), int(values[21]))
        except (OSError, ValueError, IndexError):
            continue
    # Playwright 会另建 Chromium 的进程组，必须按父子关系计量和回收，不能只匹配 PGID。
    found = {root: processes.get(root, (0, 0))[1]}
    changed = True
    while changed:
        changed = False
        for pid, (parent, pages) in processes.items():
            if parent in found and pid not in found:
                found[pid] = pages
                changed = True
    return found


def _resident_bytes(root: int) -> int:
    return sum(_process_tree(root).values()) * (os.sysconf("SC_PAGE_SIZE") if os.name == "posix" else 0)


def _kill(process: asyncio.subprocess.Process) -> None:
    if os.name == "posix":
        for pid in reversed(_process_tree(process.pid)):
            if pid != process.pid:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.returncode is None:
        process.kill()


@functools.cache
def _browser_slot() -> asyncio.Semaphore:
    return asyncio.Semaphore(1)


async def _communicate(process: asyncio.subprocess.Process) -> bytes:
    task = asyncio.create_task(process.communicate())
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=0.5)
            if not done and await asyncio.to_thread(_resident_bytes, process.pid) > 1536 << 20:
                raise ValueError("动画渲染超过内存限制")
        return (await task)[0]
    finally:
        _kill(process)
        await process.wait()
        await task


async def _run(kind: str, source: Path, directory: Path, animation: str) -> float:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "qiuyuantu.convert.special",
        kind,
        str(source),
        str(directory),
        animation,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=os.name == "posix",
    )
    try:
        output = await asyncio.wait_for(_communicate(process), 60)
    except asyncio.TimeoutError as exc:
        raise ValueError("动画渲染超时，请缩短动画或使用较小的文件") from exc
    try:
        result = json.loads(output)
    except ValueError as exc:
        raise ValueError("动画渲染失败，请检查文件或转换依赖") from exc
    if process.returncode:
        raise ValueError(result.get("error") or "动画渲染失败")
    return float(result["duration"])


async def run(kind: str, source: Path, directory: Path, animation: str = "") -> float:
    if kind == "pag":
        async with _browser_slot():
            return await _run(kind, source, directory, animation)
    return await _run(kind, source, directory, animation)
