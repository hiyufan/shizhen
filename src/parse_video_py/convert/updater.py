"""yt-dlp 自动升级：平台一改版 yt-dlp 就要跟着发版，pip 装的不会自己更新。

每隔 N 天 `pip install -U yt-dlp`；版本真的变了就等到没有任务在跑时重启进程
（用 os.execv 原地重启，容器 / systemd 都不用管）。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from importlib.metadata import version as _pkg_version

from . import jobs


def current_version() -> str:
    try:
        return _pkg_version("yt-dlp")
    except Exception:  # pragma: no cover
        return "unknown"


def _pip_upgrade() -> str:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "yt-dlp[default]"],
        check=False, timeout=600,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # 新版本要看磁盘上的 dist-info, importlib.metadata 有缓存, 另起解释器查
    out = subprocess.run(
        [sys.executable, "-c", "import importlib.metadata as m;print(m.version('yt-dlp'))"],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout.strip() or current_version()


async def loop(every_days: float) -> None:
    interval = max(1.0, every_days) * 86400
    before = current_version()
    while True:
        await asyncio.sleep(interval)
        try:
            after = await asyncio.to_thread(_pip_upgrade)
        except Exception:  # noqa: BLE001 - 升级失败下次再试
            continue
        if after == before:
            continue
        print(f"[updater] yt-dlp {before} -> {after}，等空闲后重启", flush=True)
        # 等到没有任务在跑再重启, 最多等 30 分钟
        for _ in range(180):
            if jobs.pending_count() == 0:
                break
            await asyncio.sleep(10)
        os.execv(sys.executable, [sys.executable, *sys.argv])
