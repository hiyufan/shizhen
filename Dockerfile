FROM python:3.12-slim

# ffmpeg 用于 GIF / 实况转换与 yt-dlp 合并
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# chromium 装到固定路径并放开读权限：运行时是 app 用户（10001），浏览器只读执行
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

# 第三方依赖和 Chromium 单独一层，只跟着 pyproject.toml 变。以前和源码一起装，只改一行代码的
# 部署也要重装全部依赖、重下 115 MB 的 Chromium、再导出一整层，两分半。
# 先放一个空包让 pip 按 pyproject 把依赖装上，再卸掉它，真正的代码在后面装
COPY pyproject.toml ./
RUN mkdir -p src/parse_video_py && touch src/parse_video_py/__init__.py \
    && pip install --no-cache-dir ".[web,cli,douyin]" bgutil-ytdlp-pot-provider \
    && pip uninstall -y parse-video-py && rm -rf src \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers

# yt-dlp 要跟着平台改版勤更新，以前靠每次部署重装顺带拿到最新版。上面那层缓存住以后单独刷：
# deploy.sh 把 YTDLP_REFRESH 设成当天日期，一天之内的部署共用缓存，换一天就重新拉一次
ARG YTDLP_REFRESH=""
RUN echo "yt-dlp refresh: ${YTDLP_REFRESH:-never}" && pip install --no-cache-dir --upgrade "yt-dlp[default]"

# 本项目代码：依赖都在上面了，这里只装自己
COPY README.md LICENSE main.py ./
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

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
