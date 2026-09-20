FROM python:3.12-slim

# ffmpeg 用于 GIF / 实况转换与 yt-dlp 合并
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE main.py ./
COPY src/ src/
RUN pip install --no-cache-dir ".[web,cli]" bgutil-ytdlp-pot-provider

ENV PARSE_VIDEO_DATA_DIR=/app/data \
    PARSE_VIDEO_TRUST_PROXY=1 \
    PARSE_VIDEO_MCP=0
RUN useradd -r -u 10001 -d /app app && mkdir -p /app/data && chown -R app:app /app
USER app
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

# 只跑 1 个 worker：任务状态在进程内存里。要横向扩容就多起几个容器并在 LB 上开会话保持
CMD ["python", "main.py"]
