FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
# 实验环境独立固定版本，不替换主应用使用的 yt-dlp。
RUN pip install --no-cache-dir \
    'yt-dlp[default] @ https://github.com/bashonly/yt-dlp/archive/54f9a7b75e2b4a7611fdf47aa3227b809382386e.tar.gz' \
    playwright==1.64.0 deno==2.9.8 yt-dlp-ejs==0.8.0 protobug==1.0.0 bgutil-ytdlp-pot-provider==2.0.2 \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers
COPY docker/youtube/probe.py /app/probe.py
RUN useradd -r -u 10001 -m probe && mkdir /output && chown probe:probe /output
USER probe
WORKDIR /output
ENTRYPOINT ["timeout", "300s", "python", "/app/probe.py"]
