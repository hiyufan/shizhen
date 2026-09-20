"""转换模块的运行时配置。默认所有临时文件都放在项目根目录的 data/ 下。"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("PARSE_VIDEO_DATA_DIR", PROJECT_DIR / "data"))
BIN_DIR = DATA_DIR / "bin"
SOURCES_DIR = DATA_DIR / "sources"   # 为转换而下载的原视频
OUTPUTS_DIR = DATA_DIR / "outputs"   # GIF / 实况照片结果
UPLOADS_DIR = DATA_DIR / "uploads"   # 用户上传的本地视频

# yt-dlp 的 cookies：Netscape 格式文件，或浏览器名 (firefox / edge / chrome ...)
COOKIES_FILE = Path(os.environ.get("PARSE_VIDEO_COOKIES_FILE", PROJECT_DIR / "cookies.txt"))
COOKIES_BROWSER = os.environ.get("PARSE_VIDEO_COOKIES_BROWSER", "")

# YouTube PO Token 服务 (bgutil-ytdlp-pot-provider 的 HTTP 服务地址), 公网部署防 "Sign in to confirm you're not a bot"
POT_URL = os.environ.get("PARSE_VIDEO_POT_URL", "")

# 每隔几天自动 pip 升级 yt-dlp 并在空闲时重启; 0 = 关闭
YTDLP_AUTOUPDATE_DAYS = float(os.environ.get("PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS", 0))

# 解析结果缓存: 同一个链接 N 秒内不再打平台
PARSE_CACHE_SECONDS = int(os.environ.get("PARSE_VIDEO_PARSE_CACHE", 600))
PARSE_CACHE_SIZE = 500

# 清理策略
JOB_TTL_SECONDS = int(os.environ.get("PARSE_VIDEO_JOB_TTL", 60 * 60))          # 结果保留 1 小时
SOURCE_TTL_SECONDS = int(os.environ.get("PARSE_VIDEO_SOURCE_TTL", 2 * 60 * 60))  # 原视频缓存 2 小时
MAX_CONCURRENT_JOBS = int(os.environ.get("PARSE_VIDEO_MAX_JOBS", max(1, (os.cpu_count() or 2) - 1)))
MAX_QUEUED_JOBS = int(os.environ.get("PARSE_VIDEO_MAX_QUEUE", 50))                # 排队上限，超了直接拒绝
JOB_TIMEOUT_SECONDS = int(os.environ.get("PARSE_VIDEO_JOB_TIMEOUT", 300))          # 单个任务最长 5 分钟
MAX_UPLOAD_BYTES = int(os.environ.get("PARSE_VIDEO_MAX_UPLOAD", 300 * 1024 * 1024))
MAX_SOURCE_BYTES = int(os.environ.get("PARSE_VIDEO_MAX_SOURCE", 300 * 1024 * 1024))  # 为转换拉取的原视频上限
DISK_QUOTA_BYTES = int(os.environ.get("PARSE_VIDEO_DISK_QUOTA", 8 * 1024 ** 3))       # data/ 超过就删最旧的
FFMPEG_THREADS = int(os.environ.get("PARSE_VIDEO_FFMPEG_THREADS", 0))                 # 0 = 按并发数自动分

# 转换限制
GIF_MAX_SECONDS = 30.0
LIVE_MAX_SECONDS = 10.0
LIVE_DEFAULT_SECONDS = 3.0
SOURCE_MAX_HEIGHT = 1080  # 通过 yt-dlp 拉取原视频时的最高分辨率

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
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
