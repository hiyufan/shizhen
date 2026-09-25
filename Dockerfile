# 两阶段构建：编译环境不进运行镜像，最后只有一个二进制 + ffmpeg
FROM rust:1-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock rust-toolchain.toml clippy.toml ./
COPY src src
COPY web web
COPY content content
RUN cargo build --release --locked

FROM debian:bookworm-slim
# ffmpeg 做转换与合并；ca-certificates 给 HTTPS
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/shizhen /usr/local/bin/shizhen

ENV PARSE_VIDEO_DATA_DIR=/app/data \
    PARSE_VIDEO_TRUST_PROXY=1
RUN useradd -r -u 10001 -d /app app && mkdir -p /app/data && chown -R app:app /app
USER app
WORKDIR /app
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD ["shizhen", "health"]

# 单进程：任务状态在进程内存里。要横向扩容就多起几个容器并在 LB 上开会话保持
CMD ["shizhen"]
