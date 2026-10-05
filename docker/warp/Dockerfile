# Cloudflare WARP 的本地代理，给 YouTube 当备用出口（见 docker-compose.yml 的 warp 服务）。
# 用官方客户端，不用第三方打包的镜像。WARP 的代理模式只监听 127.0.0.1，靠 socat 转到容器网卡上。
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg socat \
 && curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --dearmor -o /usr/share/keyrings/cloudflare-warp.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp.gpg] https://pkg.cloudflareclient.com/ bookworm main" > /etc/apt/sources.list.d/cloudflare-client.list \
 && apt-get update && apt-get install -y --no-install-recommends cloudflare-warp \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 1080
HEALTHCHECK --interval=60s --timeout=15s --start-period=60s \
  CMD curl -fsS -m 10 -x http://127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace | grep -q "warp=on" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
