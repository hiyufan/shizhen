# 自部署与配置

[返回 README](../README.md) · [English](self-hosting.en.md)

[生产部署](#生产部署) · [配置](#配置) · [安全](#安全) · [性能](#性能)

## 生产部署

一台 **4 核 / 8 GB / 100 Mbps** 的 VPS 就能跑。`docker-compose.yml` 带了四个服务：应用、[Caddy](https://caddyserver.com/)（自动 HTTPS 反代）、[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)（YouTube PO Token）和 WARP 备用出口。

```bash
# 域名先解析到这台机器
DOMAIN=your.domain docker compose up -d
```

不用 Docker 的话，自己起 Nginx / Caddy 反代到 `127.0.0.1:8000`，并设置 `PARSE_VIDEO_TRUST_PROXY=1` 让限流拿到真实客户端 IP。

排查问题：`docker compose exec app python -m qiuyuantu.diag "<分享链接>"`，会打印出口 IP、解析结果和平台返回的原始状态。

push 到 main 且涉及核心 / Web 运行文件时，测试通过后自动上线，自检不过自动回滚；仅改文档或账号助手只执行检查：配置方法见 [docs/deploy.md](deploy.md)。

### 海外服务器必看：小红书 / B站 会拒绝海外和机房 IP

B站 API 返回 412，小红书返回验证页。四种办法，任选其一：

**1. 给国内平台单独配一个国内出口**（推荐）

```bash
PARSE_VIDEO_PROXY_CN=http://user:pass@host:port
```

YouTube 等仍然直连。只有解析请求（流量很小）走它，视频本体默认由服务器直连 CDN，需要时再开 `PARSE_VIDEO_PROXY_CN_MEDIA=1`。出口可以是：

| 出口 | 成本 | 说明 |
|---|---|---|
| 家里电脑 / NAS / 树莓派 + Tailscale | 免费 | 住宅 IP 对平台最友好。两端装 Tailscale，家里跑 `gost -L "http://user:pass@:8888"` |
| 国内轻量 VPS | 几十元/月 | 跑同样的 gost，安全组只放行你服务器的 IP |
| 住宅代理服务商 | 按流量 | |
| ~~Cloudflare Worker / Pages~~ | – | **不行**，出口是 CF 的海外机房 IP，B站 直接 412 |

**2. 边缘函数中继**（没有国内机器时）

把 `scripts/esa-relay.js` 部署到阿里云 ESA 边缘函数（改掉里面的 `TOKEN`），国内平台的解析请求就从国内边缘节点发出：

```bash
PARSE_VIDEO_RELAY_CN=https://<函数域名>/relay
PARSE_VIDEO_RELAY_TOKEN=<同一个 TOKEN>
```

可以再开 `PARSE_VIDEO_EDGE_IMG=1` / `PARSE_VIDEO_EDGE_MEDIA=1`，让国内用户直接从边缘节点取图片和视频，不用绕海外服务器。局限：yt-dlp 走不了中继，B站 只有 480p 直链。部署、探测出口和口令的注意事项见 [docs/esa-relay.md](esa-relay.md)。

**3. 贴登录 Cookie**

```bash
PARSE_VIDEO_XHS_COOKIE="a1=...; web_session=..."
PARSE_VIDEO_BILI_COOKIE="SESSDATA=...; buvid3=..."
```

F12 → Network → 请求头里的 Cookie。B站 不登录也会自动领一份 buvid 设备指纹，多数情况已够。抖音的图文（note/slides）不用配 cookie：平台对匿名 API 收紧后，解析会自动起一个一次性无痕 Chromium 渲染 PC 版页面兜底（全程无账号）；可选再配 `PARSE_VIDEO_DOUYIN_COOKIE`，让图文走 slidesinfo 拿到全量数据（含实况视频），不配只是实况图在这条兜底路上拿不到。

**4. 部署在国内**，再用 `PARSE_VIDEO_PROXY` 给 YouTube 配海外出口。

### 风控：YouTube 提示「确认不是机器人」

完整的 YouTube 支持需要 EJS 脚本、JS 运行时和可用出口。Docker 已装 Deno；手动安装用 `pip install -e ".[web,cli,youtube]"`。`/api/health` 的 `js_runtime` 应为 `deno` 或受支持的 `node`，不能是 `null`。

compose 已配好 `PARSE_VIDEO_POT_URL=http://bgutil:4416`，插件与服务端固定为相同版本。升级时要一起更新，不要只拉一个 `latest`。

Docker 还包含独立解释器中的原生 Chromium 会话 / SABR 兜底。普通解析失败，或返回的媒体地址不可读时，会用同一浏览器会话生成内容绑定 Token，通过现有 WARP 完整下载、校验并缓存视频，随后复用预览、下载和转换。已在两个此前触发机器人验证的公开视频上验证完整下载，不需要账号 Cookie。高分辨率和音频下载失败时也可调用这条路径。

同一链接的并发解析会合并为一次抓取；已验证的 YouTube 视频按视频 ID 复用缓存，长链接、短链接和时间戳分享都能命中，缓存读取无需等待其它视频的浏览器任务。默认路径失败后，备用解析和原生兜底并行恢复，接受先完成且通过媒体验证的结果。

PO Token 不保证解除 IP 风控。其它部署可配置 `PARSE_VIDEO_PROXY_YOUTUBE`，只让 YouTube 的解析和媒体走这个出口；需要登录的内容仍需有效的 `cookies.txt`。自检会检查两条视频并读取媒体片段，单条解析成功不会把整个平台报成正常。

实测结果、限制与排查方法见 [YouTube 排障](youtube.md)。

`PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS=7` 会每周升级 yt-dlp 并在空闲时原地重启，平台改版全靠它跟进。

## 配置

全部通过环境变量，都可不填。

### 基础

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_HOST` / `PARSE_VIDEO_PORT` | `0.0.0.0` / `8000` | 监听地址 / 端口 |
| `PARSE_VIDEO_DATA_DIR` | `./data` | 缓存、结果、密钥目录 |
| `PARSE_VIDEO_SITE_URL` | 按请求 | 正式域名，用于 canonical / sitemap / OG 图 |
| `PARSE_VIDEO_TRUST_PROXY` | `0` | 反代后面设 `1`，信任 `X-Forwarded-For` |
| `PARSE_VIDEO_USERNAME` + `PARSE_VIDEO_PASSWORD` | – | 同时设置则开启 Basic Auth |
| `PARSE_VIDEO_MCP` | `1` | 设 `0` 关闭 `/mcp`（Docker 镜像默认关） |

### 解析

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_PROXY` | – | 解析时使用的 HTTP 代理（所有平台） |
| `PARSE_VIDEO_PROXY_YOUTUBE` | – | YouTube 页面、视频和图片的专用 HTTP 代理；优先于通用代理，PO Token 插件会使用同一个出口 |
| `PARSE_VIDEO_PROXY_CN` | – | 只给国内平台（抖音 / 小红书 / 快手 / B站 / 微博…）的解析用的代理，海外服务器必备 |
| `PARSE_VIDEO_PROXY_CN_MEDIA` | `0` | 设 `1` 时视频 / 图片本体的转发也走 `PROXY_CN`（CDN 被 403 时才需要，会吃代理带宽） |
| `PARSE_VIDEO_RELAY_CN` + `PARSE_VIDEO_RELAY_TOKEN` | – | 边缘函数中继地址与口令（`scripts/esa-relay.js`） |
| `PARSE_VIDEO_EDGE_IMG` | `0` | 设 `1` 时国内平台的图片由浏览器直接从中继边缘节点取（需部署带 `/img` 的 `esa-relay.js`） |
| `PARSE_VIDEO_EDGE_MEDIA` | `0` | 设 `1` 时国内平台的视频 / 音频由浏览器直接从中继边缘节点取（需部署带 `/media` 的 `esa-relay.js`；流量算在 ESA 上） |
| `PARSE_VIDEO_XHS_COOKIE` / `PARSE_VIDEO_BILI_COOKIE` / `PARSE_VIDEO_DOUYIN_COOKIE` | – | 小红书 / B站 / 抖音 的登录 Cookie 字符串，海外服务器被拦时用；抖音不配也有无痕浏览器兜底，配了图文才有实况视频 |
| `PARSE_VIDEO_COOKIES_FILE` | `./cookies.txt` | yt-dlp 用的 Netscape 格式 cookies |
| `PARSE_VIDEO_COOKIES_BROWSER` | – | 直接从浏览器读 cookies：`firefox` / `edge` / `chrome` |
| `PARSE_VIDEO_POT_URL` | – | YouTube PO Token 服务地址，如 `http://bgutil:4416` |
| `PARSE_VIDEO_YOUTUBE_NATIVE` | `1` | Docker 原生会话 / SABR 兜底；设 `0` 关闭，手动安装还须配置隔离解释器 |
| `PARSE_VIDEO_YOUTUBE_NATIVE_PROXY` | – | 兜底专用的无认证 HTTP 代理；默认依次使用 YouTube 专用出口、WARP、通用出口 |
| `PARSE_VIDEO_YOUTUBE_NATIVE_TIMEOUT` | `75` | 单次原生会话下载与完整校验的秒数上限；还受接口 / 任务总超时约束 |
| `PARSE_VIDEO_YOUTUBE_HLS` | `1` | 兜底时先经同一出口下 HLS（不开浏览器，约 10 秒），没走通再开浏览器；设 `0` 只走浏览器 |
| `PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS` | `0` | 每隔几天自动升级 yt-dlp 并在空闲时重启 |
| `PARSE_VIDEO_PARSE_CACHE` | `600` | 解析结果缓存秒数 |
| `PARSE_VIDEO_DNS_TTL` | `300` | SSRF 检查的域名解析缓存秒数 |

### 配额与限流

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_MAX_JOBS` | CPU 核数 − 1 | 同时进行的下载 / 转换任务数 |
| `PARSE_VIDEO_MAX_QUEUE` / `PARSE_VIDEO_JOB_TIMEOUT` | `50` / `300` | 排队上限 / 单任务超时秒数 |
| `PARSE_VIDEO_MAX_JOBS_PER_IP` / `PARSE_VIDEO_MAX_STREAMS_PER_IP` | `2` / `4` | 单 IP 同时任务数 / 同时下载流数 |
| `PARSE_VIDEO_RL_PARSE` / `_RL_JOB` / `_RL_UPLOAD` / `_RL_PROXY` | `10` / `20` / `10` / `240` | 单 IP：每分钟解析数 / 每 10 分钟任务数 / 每小时上传数 / 每分钟代理请求数 |
| `PARSE_VIDEO_PROXY_GB_PER_DAY` | `10` | 单 IP（IPv6 按 /64）每天经 `/api/proxy` 转发的下载量上限，北京时间零点清零；`0` 不限 |
| `PARSE_VIDEO_MAX_UPLOAD` / `PARSE_VIDEO_MAX_SOURCE` | 300 MB | 上传 / 原视频大小上限（字节） |
| `PARSE_VIDEO_DISK_QUOTA` | 8 GB | `data/` 目录配额，超了删最旧的 |
| `PARSE_VIDEO_SOURCE_TTL` / `PARSE_VIDEO_JOB_TTL` | 2 h / 1 h | 原视频缓存 / 转换结果保留时间 |
| `PARSE_VIDEO_FFMPEG_THREADS` | 自动 | 每个 ffmpeg 的线程数，默认按并发数分摊 |

### 安全与 SEO

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_SECRET` | 自动生成到 `data/secret.key` | 代理链接签名密钥，多实例必须统一 |
| `PARSE_VIDEO_SSRF_DNS` | `1` | 设 `0` 关闭域名解析检查（仅本机开着 fake-ip 代理调试时） |
| `PARSE_VIDEO_SITE_VERIFICATION` | – | 站长平台验证 `<meta>` 片段，原样输出到 `<head>` |
| `PARSE_VIDEO_ANALYTICS` | – | 统计代码片段（百度统计 / Umami / GA），输出到页面底部 |

### 使用统计

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_STATS_TOKEN` | – | 设了才开始记录；打开 `/stats?token=<它>` 看每小时 / 每天有多少人在用、各平台成功率、失败原因、任务耗时 |
| `PARSE_VIDEO_STATS_IGNORE_IPS` | – | 不计入统计的 IP（逗号分隔），填服务器自己的公网地址；本机 / 内网地址本来就不计。站长自己的设备打开 `/test` 用统计口令开一次测试模式 |
| `PARSE_VIDEO_FEEDBACK_REPO` / `_GH_TOKEN` / `_KEY` | – | 解析失败反馈：建 issue 的私有仓库、只给它 Issues 读写的 fine-grained token、加密反馈邮箱的 32 字节密钥（base64）。和下面的 SMTP 四样都填了才显示「反馈这个问题」 |
| `PARSE_VIDEO_SMTP_USER` / `_PASS` / `_HOST` / `_PORT` | – / – / `smtp.qq.com` / `465` | 反馈修好后发通知邮件的发件邮箱和 SMTP 授权码 |
| `PARSE_VIDEO_ALERT_TO` | – | 站长收告警的邮箱（用上面的 SMTP 发）。每小时自检后判断：一半以上平台连续两轮超时 / 连不上（多半是服务器自己断网了），或某个平台连续三轮解析失败（多半改版了），就发一封；恢复了再发一封，同一个问题不重复发 |
| `PARSE_VIDEO_STATS_DAYS` | `90` | 明细保留天数 |

数据在 `data/stats.db`（SQLite）。只记事件：成功的解析不记链接，IP 经密钥 HMAC 后只留 12 位，能数出人数还原不出是谁。解析失败的链接另存在 `data/failures.db`，只留 7 天，统计页上列出来方便排查（站长测试设备和服务器自己的请求不记）。`/api/stats?range=7d&token=…` 直接拿 JSON。

## 安全

这是一个会替用户抓任意链接、再把文件转发回去的服务，公开部署时主要防两件事：被当成开放代理 / SSRF 跳板，以及被恶意媒体文件打崩。

| 措施 | 做法 |
| --- | --- |
| 链接签名 | 代理、准备、下载、实况打包只接受 `/api/parse` 签过名的地址（HMAC），别人不能拿你的服务器代理任意网址 |
| SSRF 防护 | 字面内网 IP、localhost、`.internal`、云元数据地址一律拒绝；域名先解析再放行；302 跳转每一跳都检查，上游解析器同样覆盖 |
| 连接隔离 | 出站连接池复用连接但不共享 Cookie jar，不同请求之间不会串会话 |
| 响应头 | CSP、`X-Frame-Options: DENY`、`nosniff`、`Referrer-Policy`；`/docs` `/openapi.json` 关闭 |
| 进程 | 容器以非 root 运行；ffmpeg / yt-dlp 走参数列表不经 shell；任务超时或取消会杀掉子进程；错误信息不带服务器路径 |
| 配额 | 按 IP 限流、并发任务数、上传与原视频上限、磁盘配额 |

仍需注意：ffmpeg 和 yt-dlp 处理的是不可信媒体，应保持更新（yt-dlp 可自动升级，ffmpeg 通过重建镜像更新）；`cookies.txt` 属于账号凭证，只应存放在服务器上；服务本身没有登录机制，限流针对的是脚本滥用，带宽成本需要部署者自行评估。

## 性能

出站连接全程复用（解析、中继、CDN 下载共用连接池），省掉重复的 DNS + TCP + TLS 握手。同一台机器上实测的前后对比：

| | 优化前 | 优化后 | |
|---|---:|---:|:-:|
| B站 解析（5 个视频中位数） | 2039 ms | **699 ms** | −66% |
| 单次中继调用 | 1098 ms | **268 ms** | −76% |
| 256 KB Range 请求（播放器拖动） | 1457 ms | **449 ms** | −69% |
| 代理下载吞吐 | 1.5 MB/s | **1.9 MB/s** | +27% |

当前表现：

| 场景 | 结果 |
|---|---|
| 页面渲染 | p50 **1.8 ms**，并发 50 时 p95 49 ms，单 worker 约 **730 QPS** |
| GIF 转换（20s / 640px / 15fps） | **1.9 s**，转换期间页面 p50 仅从 1.8 ms 升到 3.1 ms |
| 109 MB 上传 | 0.64 s，期间页面延迟最大抖动 37 ms |
| 空闲占用 | CPU 0.13%，内存 57 MB |

<details>
<summary>测试环境与注意事项</summary>

<br>

4 核 VPS、Docker 部署、海外出口经阿里云 ESA 边缘函数中继访问国内平台。压测走 loopback，未经反代。

这些数字偏乐观，实际部署要打折看：

- 转换用的是 `testsrc` 合成视频，压缩起来比真实素材轻松，真实内容的 GIF 转换预计慢 2–5 倍
- 未计入真实网络的 RTT 和反代开销
- **解析耗时的大头是等平台响应，与服务器性能无关**。上面 699 ms 里绝大部分是中继到国内平台的往返

容量瓶颈按顺序：带宽（视频经服务器转发，100 Mbps 约支持 10 个并发下载）→ ffmpeg 的 CPU（每个 GIF 占一个核数秒）→ 平台风控（对单个出口 IP 限流）。配额与限流内置，默认值按 4 核、几百日活设定，均可用环境变量调整。

**扩容**：任务状态在进程内存里。横向扩容需要多起几个容器 + 负载均衡开会话保持（Cookie 或 IP hash）+ 统一 `PARSE_VIDEO_SECRET`。注意任务产物是本机磁盘文件，真正的无状态扩容除了共享任务状态，还需要共享存储或实例间转发。

</details>
