"""转换模块的运行时配置。默认所有临时文件都放在项目根目录的 data/ 下。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("PARSE_VIDEO_DATA_DIR", PROJECT_DIR / "data"))
BIN_DIR = DATA_DIR / "bin"
SOURCES_DIR = DATA_DIR / "sources"  # 为转换而下载的原视频
OUTPUTS_DIR = DATA_DIR / "outputs"  # GIF / 实况照片结果
UPLOADS_DIR = DATA_DIR / "uploads"  # 用户上传的本地视频

# yt-dlp 的 cookies：Netscape 格式文件，或浏览器名 (firefox / edge / chrome ...)
COOKIES_FILE = Path(os.environ.get("PARSE_VIDEO_COOKIES_FILE", PROJECT_DIR / "cookies.txt"))
COOKIES_BROWSER = os.environ.get("PARSE_VIDEO_COOKIES_BROWSER", "")

# YouTube PO Token 服务 (bgutil-ytdlp-pot-provider 的 HTTP 服务地址), 公网部署防 "Sign in to confirm you're not a bot"
POT_URL = os.environ.get("PARSE_VIDEO_POT_URL", "")

# 原生 YouTube 会话 / SABR 兜底使用隔离解释器，不能替换普通平台的 yt-dlp。
YOUTUBE_NATIVE_PYTHON = os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE_PYTHON", "")
YOUTUBE_NATIVE_SCRIPT = Path(
    os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE_SCRIPT", PROJECT_DIR / "docker/youtube/worker.py")
)
YOUTUBE_NATIVE_TIMEOUT = float(os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE_TIMEOUT", "75"))
# 解析时整条视频要在上面的时间里下完：开浏览器约 15 秒，经 WARP 实测 6.7 MB/s（2026-10-22，
# 10 分钟 720p60 共 182 MB 下了 27 秒）。估算超过这个大小就自动降一档清晰度，最低一档也放不下就直说
YOUTUBE_NATIVE_PARSE_BYTES = int(os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE_PARSE_BYTES", 120 * 1024 * 1024))
# 开浏览器之前先经同一出口用 yt-dlp 并发拉 HLS 分片：受限视频的 TV 直链经 WARP 一律 403，HLS 却能下，
# 实测 10 分钟 720p 18 MB 8 秒、161 MB 13 秒（2026-10-22，约 12 MB/s）。设 0 关闭
YOUTUBE_HLS = os.environ.get("PARSE_VIDEO_YOUTUBE_HLS", "1") != "0"
# 解析时 HLS 能下多大：比浏览器路径快得多，给得比上面宽；HLS 没走通时还要留时间开浏览器
YOUTUBE_HLS_PARSE_BYTES = int(os.environ.get("PARSE_VIDEO_YOUTUBE_HLS_PARSE_BYTES", 200 * 1024 * 1024))
# 同时开几个浏览器会话：每个约 0.5 GB 内存
YOUTUBE_NATIVE_CONCURRENCY = int(os.environ.get("PARSE_VIDEO_YOUTUBE_NATIVE_CONCURRENCY", "2"))

# 每隔几天自动 pip 升级 yt-dlp 并在空闲时重启; 0 = 关闭
YTDLP_AUTOUPDATE_DAYS = float(os.environ.get("PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS", 0))

# 解析结果缓存: 同一个链接 N 秒内不再打平台
PARSE_CACHE_SECONDS = int(os.environ.get("PARSE_VIDEO_PARSE_CACHE", 600))
PARSE_CACHE_SIZE = 500

# 清理策略
JOB_TTL_SECONDS = int(os.environ.get("PARSE_VIDEO_JOB_TTL", 60 * 60))  # 结果保留 1 小时
SOURCE_TTL_SECONDS = int(os.environ.get("PARSE_VIDEO_SOURCE_TTL", 2 * 60 * 60))  # 原视频缓存 2 小时
MAX_CONCURRENT_JOBS = int(os.environ.get("PARSE_VIDEO_MAX_JOBS", max(1, (os.cpu_count() or 2) - 1)))
MAX_QUEUED_JOBS = int(os.environ.get("PARSE_VIDEO_MAX_QUEUE", 50))  # 排队上限，超了直接拒绝
JOB_TIMEOUT_SECONDS = int(os.environ.get("PARSE_VIDEO_JOB_TIMEOUT", 300))  # 单个任务最长 5 分钟
# 下载 / 转换任务里的原生兜底：不受解析的 90 秒限制，给到任务上限前留 30 秒合并、转换
YOUTUBE_NATIVE_JOB_TIMEOUT = max(YOUTUBE_NATIVE_TIMEOUT, JOB_TIMEOUT_SECONDS - 30)
MAX_UPLOAD_BYTES = int(os.environ.get("PARSE_VIDEO_MAX_UPLOAD", 300 * 1024 * 1024))
MAX_SOURCE_BYTES = int(os.environ.get("PARSE_VIDEO_MAX_SOURCE", 300 * 1024 * 1024))  # 为转换拉取的原视频上限
DISK_QUOTA_BYTES = int(os.environ.get("PARSE_VIDEO_DISK_QUOTA", 8 * 1024**3))  # data/ 超过就删最旧的
FFMPEG_THREADS = int(os.environ.get("PARSE_VIDEO_FFMPEG_THREADS", 0))  # 0 = 按并发数自动分

# 转换限制
GIF_MAX_SECONDS = 30.0
LIVE_MAX_SECONDS = 10.0
LIVE_DEFAULT_SECONDS = 3.0
SOURCE_MAX_HEIGHT = 1080  # 通过 yt-dlp 拉取原视频时的最高分辨率

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def ensure_dirs() -> None:
    for d in (DATA_DIR, BIN_DIR, SOURCES_DIR, OUTPUTS_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def ytdlp_cookie_opts() -> dict:
    """给 yt-dlp 的 cookies / PO Token 参数；都没配置时返回空 dict。"""
    opts: dict = {}
    if COOKIES_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    elif COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    if POT_URL:
        opts["extractor_args"] = {"youtubepot-bgutilhttp": {"base_url": [POT_URL]}}
    return opts


def ytdlp_runtime_opts() -> dict:
    """EJS 需要本机 JS 运行时。直接用 .venv/bin/python 启动时，deno 未必在 PATH。"""
    deno = shutil.which("deno")
    if not deno:
        try:
            from deno import find_deno_bin
        except ImportError:
            pass
        else:
            deno = find_deno_bin()
    if deno:
        return {"js_runtimes": {"deno": {"path": str(deno)}}}
    # Node 由 yt-dlp 检查是否满足 >=22；它默认只启用 Deno，需要显式打开。
    if node := shutil.which("node"):
        return {"js_runtimes": {"node": {"path": node}}}
    return {}
