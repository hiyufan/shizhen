<div align="center">

<img src="docs/logo.svg" width="72" alt="拾帧">

# 拾帧 · Shizhen

**粘贴一个链接，取出无水印视频和原图，一键做成 GIF、iPhone 实况照片或安卓动态照片。**

支持抖音、小红书、快手、YouTube、X、B站等 35 个平台。自部署，免费，不用登录。

[![License](https://img.shields.io/badge/license-MIT-1a1815.svg)](LICENSE)
[![Rust](https://img.shields.io/badge/rust-1.85%2B-E8702A.svg)](Cargo.toml)
[![alcedo](https://img.shields.io/badge/parser-alcedo-4DB899.svg)](https://github.com/hiyufan/alcedo)
[![Docker](https://img.shields.io/badge/docker-compose%20ready-1a1815.svg)](docker-compose.yml)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-6BAFDF.svg)](#-贡献)

### [🌐 在线体验 ynvan.com](https://ynvan.com)

[快速开始](#-快速开始) · [功能](#-功能) · [部署](#-生产部署) · [性能](#-性能) · [配置](#%EF%B8%8F-配置) · [API](#-api) · [架构](#%EF%B8%8F-架构) · [安全](#-安全) · [贡献](#-贡献)

</div>

<br>

<p align="center">
  <img src="docs/screenshot-home.png" width="100%" alt="首页：粘贴链接即可解析">
</p>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshot-convert.png" alt="转换面板：拖缩略图条选段，生成实况照片"></td>
    <td width="50%"><img src="docs/screenshot-guide.png" alt="教程页"></td>
  </tr>
  <tr>
    <td align="center"><sub>任意视频截一段做成实况照片，封面帧自己挑</sub></td>
    <td align="center"><sub>教程页：每篇对准一个具体问题</sub></td>
  </tr>
</table>

---

## 为什么做这个

短视频平台把内容锁在 App 里：保存的视频带水印，图片被压缩，实况图只剩静态一帧，想做个表情包还得再找一个工具。拾帧把这几步合成一次粘贴：

**解析** — 贴上分享链接，拿到平台给网页端的无水印播放地址和原始尺寸图片
**转换** — 在缩略图条上拖出想要的几秒，直接生成 GIF、实况照片或动态照片
**原样打包** — 小红书 / 抖音自带的实况图，原图和短视频配好对，导入相册就会动

解析交给 [alcedo](https://github.com/hiyufan/alcedo)（纯 Rust 的视频平台解析库），本项目补上转换能力，做成一个可以公开部署的网站。整个服务是一个 Rust 二进制，外加 ffmpeg。

## ✨ 功能

<table>
<tr><td width="33%" valign="top">

**提取**

- 无水印视频原始码流
- 小红书原图（常见 1920×2560）
- 抖音 H.265 高清档 + 背景音乐
- YouTube / B站 服务端合并 1080p–4K（分段并发下载）
- 实况图识别与整篇打包
- 直链代理，浏览器直接播放不 403
- 失败按「已删除 / 需登录 / 被限流」分类

</td><td width="33%" valign="top">

**转换**

- **GIF** ≤30s，帧率 5–30、宽度 160–960、速度 0.5–3×，两遍调色板编码
- **iPhone 实况** ≤10s，JPG + MOV 配对，`ContentIdentifier` 双向写入并回读校验
- **安卓动态照片** 内嵌 MP4 的 JPG，Motion Photo + MicroVideo XMP
- **原样打包** 平台实况图不重编码，只换封装
- **本地视频** 上传即转，≤300 MB

</td><td width="33%" valign="top">

**网站**

- 编辑风极简界面，手机端完整适配
- 字体全部自托管，页面零外部请求
- 8 个落地页 + 7 篇教程，JSON-LD 结构化数据
- 公网加固：限流、配额、签名、SSRF、CSP
- 单个二进制，常驻内存约 18 MB，Docker 非 root 运行
- 站长统计页 `/stats`：每小时 / 每天多少人在用、各平台成功率、失败原因

</td></tr>
</table>

## 🌐 支持的平台

| 平台 | 视频 | 图集 | 实况图 | 清晰度 | 说明 |
|---|:-:|:-:|:-:|---|---|
| 抖音 | ✅ | ✅ | ✅ | 720p – 1080p，H.265 | 背景音乐可单独下载 |
| 小红书 | ✅ | ✅ | ✅ | 最高档 | 链接需带有效 `xsec_token`，现复制现用 |
| 快手 | ✅ | ✅ | – | 平台默认 | |
| YouTube | ✅ | – | – | 直链 + 720p – 4K 服务端合并 | |
| X (Twitter) | ✅ | ✅ | – | 最高码率 | |
| B站 | ✅ | – | – | 720p 直链，1080p 服务端合并 | 不登录也能拿到 1080p；b23.tv 短链、av 号可用 |
| 微博、西瓜、皮皮虾、AcFun、TikTok、Instagram、Vimeo … | ✅ | 部分 | – | | 共 35 个平台，完整清单见 alcedo |

> 解析依赖各平台接口，平台改版可能暂时失效，修复在 [alcedo](https://github.com/hiyufan/alcedo) 里进行，这边升级依赖版本即可。

## 🚀 快速开始

需要 [Rust 工具链](https://rustup.rs) 和 ffmpeg（PATH 上的，或者放在 `data/bin/ffmpeg(.exe)`）。

```bash
git clone https://github.com/hiyufan/shizhen.git && cd shizhen
./start.sh          # Windows: .\start.ps1
```

编译完成后打开 <http://127.0.0.1:8000>。模板、样式、字体和文案都编进了二进制，`target/release/shizhen` 拷到哪都能跑。

<details>
<summary>单容器 Docker</summary>

```bash
docker build -t shizhen .
docker run -d -p 8000:8000 -v ./data:/app/data shizhen
```

</details>

## 🐳 生产部署

一台 **4 核 / 8 GB / 100 Mbps** 的 VPS 就能跑，瓶颈在带宽和 ffmpeg，应用本身很轻。`docker-compose.yml` 带了两个服务：应用（镜像里只有 `shizhen` 二进制和 ffmpeg）和 [Caddy](https://caddyserver.com/)（自动 HTTPS 反代）。

```bash
# 域名先解析到这台机器
DOMAIN=your.domain PARSE_VIDEO_SITE_URL=https://your.domain docker compose up -d
```

不用 Docker 的话，自己起 Nginx / Caddy 反代到 `127.0.0.1:8000`，并设置 `PARSE_VIDEO_TRUST_PROXY=1` 让限流拿到真实客户端 IP。

排查问题：`docker compose exec app shizhen diag "<分享链接>"`，会打印国内平台走的出口、服务器出口 IP、解析结果和耗时。

<details>
<summary><b>海外服务器必看</b> — 小红书 / B站 会拒绝海外和机房 IP</summary>

<br>

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

把 `scripts/esa-relay.js` 部署到阿里云 ESA 边缘函数 / 边缘 Pages，出口在国内边缘节点。改掉里面的 `TOKEN`，然后：

```bash
PARSE_VIDEO_RELAY_CN=https://<函数域名>/relay
PARSE_VIDEO_RELAY_TOKEN=<同一个 TOKEN>
```

国内平台的解析请求由边缘节点代发，跳转仍由本地逐跳做 SSRF 检查。先开 `https://<函数域名>/probe?xhs=<小红书链接>` 看边缘出口能不能过。

再设 `PARSE_VIDEO_EDGE_IMG=1`，国内平台（小红书 / 抖音 / 快手 / B站 / 微博）的图片就由浏览器直接从边缘节点的 `/img` 取，不再走「国内 CDN → 海外服务器 → 国内用户」跨两次太平洋；边缘取不到时自动回退到服务器转发。`/img` 只认服务器签过名、未过期的地址，只转白名单里的图片 CDN、只回 `image/*`，不会变成通用代理。

局限：中继只转发解析请求（流量很小）；视频本体仍由服务器直连 CDN 下载，B站 1080p 合并下载能不能成取决于服务器自身出口能否访问 B站 CDN。

**3. 贴登录 Cookie**

```bash
PARSE_VIDEO_XHS_COOKIE="a1=...; web_session=..."
PARSE_VIDEO_BILI_COOKIE="SESSDATA=...; buvid3=..."
```

F12 → Network → 请求头里的 Cookie。B站 不登录也会自动领一份 buvid 设备指纹，未登录也能拿到 1080p，多数情况已够。

**4. 部署在国内**，再用 `PARSE_VIDEO_PROXY` 给 YouTube 配海外出口。

</details>

## ⚡ 性能

解析的耗时绝大部分是在等平台响应；服务端做的事是尽量少发请求、不等不该等的：常用平台的连接启动时预热并定期保温，出站连接全程复用，B站 高清档位直接从 DASH 接口拿，不再额外跑一遍解析器。本机直连实测：

| | Python 版 | Rust 版 |
|---|---:|---:|
| B站 解析（热连接 p50） | ~1110 ms | **~160 ms** |
| 抖音 解析（热连接 p50 / p90） | 237–307 / 300–386 ms | **~210 / ~235 ms** |
| B站 1080p 合并下载（75 MB） | – | **24 s**（单连接要 109 s） |
| 常驻内存 | 83 MB | **18 MB** |
| 部署物 | Python 环境 + 依赖 + yt-dlp | 13 MB 二进制 + ffmpeg |

合并下载快是因为各家 CDN 普遍按连接限速（实测 B站 单连接 ~0.2 MB/s），服务端把文件切成 2 MB 一段、6 段并发拉取。

<details>
<summary>容量与扩容</summary>

<br>

容量瓶颈按顺序：带宽（视频经服务器转发，100 Mbps 约支持 10 个并发下载）→ ffmpeg 的 CPU（每个 GIF 占一个核数秒）→ 平台风控（对单个出口 IP 限流）。配额与限流内置，默认值按 4 核、几百日活设定，均可用环境变量调整。

**扩容**：任务状态在进程内存里。横向扩容需要多起几个容器 + 负载均衡开会话保持（Cookie 或 IP hash）+ 统一 `PARSE_VIDEO_SECRET`。任务产物是本机磁盘文件，真正的无状态扩容除了共享任务状态，还需要共享存储或实例间转发。

</details>

## ⚙️ 配置

全部通过环境变量，都可不填。

<details open>
<summary><b>基础</b></summary>

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_HOST` / `PARSE_VIDEO_PORT` | `0.0.0.0` / `8000` | 监听地址 / 端口 |
| `PARSE_VIDEO_DATA_DIR` | `./data` | 缓存、结果、密钥目录 |
| `PARSE_VIDEO_SITE_URL` | 按请求 | 正式域名，用于 canonical / sitemap / OG 图 |
| `PARSE_VIDEO_TRUST_PROXY` | `0` | 反代后面设 `1`，信任 `X-Forwarded-For` |
| `PARSE_VIDEO_USERNAME` + `PARSE_VIDEO_PASSWORD` | – | 同时设置则开启 Basic Auth |
| `RUST_LOG` | `info,alcedo=warn` | 日志级别 |

</details>

<details>
<summary><b>解析</b></summary>

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_PROXY` | – | 解析时使用的 HTTP 代理（所有平台） |
| `PARSE_VIDEO_PROXY_CN` | – | 只给国内平台（抖音 / 小红书 / 快手 / B站 / 微博…）的解析用的代理，海外服务器必备 |
| `PARSE_VIDEO_PROXY_CN_MEDIA` | `0` | 设 `1` 时视频 / 图片本体的转发也走 `PROXY_CN`（CDN 被 403 时才需要，会吃代理带宽） |
| `PARSE_VIDEO_RELAY_CN` + `PARSE_VIDEO_RELAY_TOKEN` | – | 边缘函数中继地址与口令（`scripts/esa-relay.js`） |
| `PARSE_VIDEO_EDGE_IMG` | `0` | 设 `1` 时国内平台的图片由浏览器直接从中继边缘节点取（需部署带 `/img` 的 `esa-relay.js`） |
| `PARSE_VIDEO_XHS_COOKIE` / `PARSE_VIDEO_BILI_COOKIE` | – | 小红书 / B站 的登录 Cookie 字符串，海外服务器被拦时用 |
| `PARSE_VIDEO_DOUYIN_COOKIE` / `PARSE_VIDEO_YOUTUBE_COOKIE` | – | 抖音 / YouTube 的 Cookie（可选） |
| `PARSE_VIDEO_PARSE_CACHE` | `600` | 解析结果缓存秒数；不会超过直链自身的过期时间 |

解析相关的配置由 alcedo 读取，`ALCEDO_*` 前缀的同名变量同样有效，见 [alcedo 的配置说明](https://github.com/hiyufan/alcedo#配置)。

</details>

<details>
<summary><b>配额与限流</b></summary>

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_MAX_JOBS` | CPU 核数 − 1 | 同时进行的下载 / 转换任务数 |
| `PARSE_VIDEO_MAX_QUEUE` / `PARSE_VIDEO_JOB_TIMEOUT` | `50` / `300` | 排队上限 / 单任务超时秒数 |
| `PARSE_VIDEO_MAX_JOBS_PER_IP` / `PARSE_VIDEO_MAX_STREAMS_PER_IP` | `2` / `4` | 单 IP 同时任务数 / 同时下载流数 |
| `PARSE_VIDEO_RL_PARSE` / `_RL_JOB` / `_RL_UPLOAD` / `_RL_PROXY` | `30` / `20` / `10` / `240` | 单 IP：每分钟解析数 / 每 10 分钟任务数 / 每小时上传数 / 每分钟代理请求数 |
| `PARSE_VIDEO_MAX_UPLOAD` / `PARSE_VIDEO_MAX_SOURCE` | 300 MB | 上传 / 原视频大小上限（字节） |
| `PARSE_VIDEO_DISK_QUOTA` | 8 GB | `data/` 目录配额，超了删最旧的 |
| `PARSE_VIDEO_SOURCE_TTL` / `PARSE_VIDEO_JOB_TTL` | 2 h / 1 h | 原视频缓存 / 转换结果保留时间 |
| `PARSE_VIDEO_FFMPEG_THREADS` | 自动 | 每个 ffmpeg 的线程数，默认按并发数分摊 |

</details>

<details>
<summary><b>安全与 SEO</b></summary>

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_SECRET` | 自动生成到 `data/secret.key` | 代理链接签名密钥，多实例必须统一 |
| `PARSE_VIDEO_SSRF_DNS` | `1` | 设 `0` 关闭域名解析检查（仅本机开着 fake-ip 代理调试时） |
| `PARSE_VIDEO_SITE_VERIFICATION` | – | 站长平台验证 `<meta>` 片段，原样输出到 `<head>` |
| `PARSE_VIDEO_ANALYTICS` | – | 统计代码片段（百度统计 / Umami / GA），输出到页面底部 |

</details>

<details>
<summary><b>使用统计</b></summary>

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARSE_VIDEO_STATS_TOKEN` | – | 设了才开始记录；打开 `/stats?token=<它>` 看每小时 / 每天有多少人在用、各平台成功率、失败原因、任务耗时 |
| `PARSE_VIDEO_STATS_DAYS` | `90` | 明细保留天数 |

数据在 `data/stats.db`（SQLite）。只记事件不记内容：链接不存，IP 经密钥 HMAC 后只留 12 位，能数出人数还原不出是谁。`/api/stats?range=7d&token=…` 直接拿 JSON。

</details>

## 🔌 API

网页用的就是这几个接口，快捷指令、脚本、自己的 App 都能接。上游 parse-video-py 的 `/video/share/url/parse`、`/video/id/parse` 保留。

```bash
curl -s "https://ynvan.com/api/parse?url=https://v.douyin.com/xxxx/"
```

```jsonc
{
  "code": 200,
  "data": {
    "source": "douyin",
    "title": "…",
    "author": { "name": "…" },
    "duration": 25.2, "width": 720, "height": 1280,
    "video_url": "https://…/play/….mp4",
    "music_url": "https://….mp3",
    "images": [{ "url": "…", "live_photo_url": "…" }],
    "formats": [{ "label": "720p H.265", "url": "…", "filesize": 126185059 }],
    "sig": { "https://…/play/….mp4": "3f9c…" }          // 每个地址的签名，下面的接口都要带
  }
}
```

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/parse?url=` | 解析分享文字 / 链接。失败时返回 `reason`：`deleted` `login` `blocked` `restricted` `network` `timeout` `unsupported` `empty` `parse` |
| `GET` | `/api/proxy?url=&sig=&filename=&download=1` | 转发直链，支持 Range；只接受解析结果里签过名的地址 |
| `POST` | `/api/prepare` | `{url \| page_url \| format_spec, sig, title}` 把原视频缓存到服务端，返回 `source_id` 或后台任务 |
| `POST` | `/api/upload` | multipart 上传本地视频，返回 `source_id` |
| `GET` | `/api/source/{id}` · `/strip?n=` | 原视频（可拖动播放）· 缩略图条 |
| `POST` | `/api/convert` | `{source_id, format: gif\|livephoto\|motionphoto, start, end, fps, width, dither, speed, key_time}` |
| `POST` | `/api/live` | `{items: [{image_url, video_url, image_sig, video_sig}], format, title}` 平台实况图原样打包 |
| `POST` | `/api/download` | `{format_spec, title}` 服务端合并下载；`format_spec` 是清晰度列表里给的签名令牌 |
| `GET` | `/api/jobs/{id}` · `/file` · `/preview` · `/video` | 任务进度 · 结果文件 · 实况封面 · 实况 MOV |
| `DELETE` | `/api/jobs/{id}` | 取消任务（仅发起者） |
| `GET` | `/api/health` | 队列、磁盘、缓存、版本 |

任务接口都返回 `{id, status: queued|running|done|error, progress, message, filename, filesize}`，轮询 `/api/jobs/{id}` 直到 `done` 后取 `/file`。限流触发时返回 `429` 并带 `Retry-After`。

## 🏗️ 架构

```mermaid
flowchart LR
    U[浏览器 / 脚本] -->|/api/parse| P[解析服务<br/>缓存 · 签名]
    P --> A[alcedo<br/>35 个平台]
    P -->|签名后的地址与令牌| U
    U -->|/api/proxy| X[直链代理<br/>Referer · Range · SSRF 检查]
    U -->|/api/prepare · convert · live · download| J[任务队列<br/>限流 · 配额 · 超时 · 取消]
    J --> D[分段并发下载]
    J --> F[ffmpeg<br/>GIF · MOV · 合并 · 抽帧]
    J --> L[实况元数据<br/>Apple ContentIdentifier · Motion Photo XMP]
    J -->|/api/jobs| U
```

分层是单向的：`web`（HTTP）→ `parse` / `jobs` / `site`（业务）→ `net` / `media` / `store`（能力）。处理函数只做"取参数、调业务、拼响应"。

```
src/
  main.rs                 入口：shizhen [serve | diag | paths | health]
  config.rs               所有环境变量，启动时读一次
  app.rs                  组装各组件、后台任务（清理、统计落盘、连接保温）
  web/                    路由与中间件：页面、解析、代理、转换、任务、统计
  parse/                  alcedo + 结果缓存 + 转成前端的 JSON；媒体令牌
  jobs/                   任务队列（排队、并发、超时、取消）与进度
  jobs/tasks/             任务本体：拉原视频、转换、实况打包、合并下载
  media/                  ffmpeg 封装；Apple MakerNote / Motion Photo XMP
  net/                    直链下载（分段并发）、SSRF、签名、Referer、边缘取图
  site/                   页面渲染（minijinja）、JSON-LD、sitemap
  store.rs · limits.rs    原视频缓存与磁盘配额 · 按 IP 限流与并发
  stats.rs                使用统计：SQLite 事件表、按时间分桶汇总
web/templates · web/static  页面模板（Jinja 语法）与自托管字体、样式
content/*.toml            落地页与教程文案
scripts/push_urls.sh      百度主动推送 + sitemap ping
scripts/esa-relay.js      阿里云 ESA 边缘函数：/probe 探测出口，/relay 国内中转，/img 边缘取图
scripts/subset_fonts.py   改文案后重建衬线字体子集（开发工具）
```

几个贯穿全局的约定：

- **没有回调**。进度是一份共享状态，下载、ffmpeg 往里写；每个 IP 的并发名额是随任务移动的守卫对象，任务结束、失败、超时、取消时自动归还
- **取消就是丢弃 future**。ffmpeg 子进程 `kill_on_drop`，正在写的文件由 `PartialFile` 的 `Drop` 删除，不需要取消标志
- **前端契约不变**。`/api/*` 的 JSON 和 Python 版逐字段一致，链接签名算法逐字节一致，前端 JS 不用改

## 🔒 安全

这是一个会替用户抓任意链接、再把文件转发回去的服务，公开部署时主要防两件事：被当成开放代理 / SSRF 跳板，以及被恶意媒体文件打崩。

- **链接签名** — 代理、准备、下载、实况打包只接受 `/api/parse` 签过名的地址（HMAC），别人不能拿你的服务器代理任意网址
- **SSRF 防护** — 字面内网 IP、localhost、`.internal`、云元数据地址一律拒绝；域名在 DNS 层检查，连接不到内网地址；302 跳转每一跳都检查，解析器同样覆盖
- **连接隔离** — 出站连接池复用连接但不共享 Cookie jar，不同请求之间不会串会话
- **合并令牌** — 服务端合并的音视频地址装在 HMAC 签名的令牌里，改一个字节就被拒绝
- **响应头** — CSP、`X-Frame-Options: DENY`、`nosniff`、`Referrer-Policy`
- **进程** — 容器以非 root 运行；代码里禁止 `unsafe`；ffmpeg 走参数列表不经 shell；任务超时或取消会杀掉子进程；错误信息不带服务器路径
- **配额** — 按 IP 限流、并发任务数、上传与原视频上限、磁盘配额

仍需注意：ffmpeg 处理的是不可信媒体，应通过重建镜像保持更新；平台 Cookie 属于账号凭证，只应放在服务器的环境变量里；服务本身没有登录机制，限流针对的是脚本滥用，带宽成本需要部署者自行评估。

## 🔍 SEO

- 首页拿泛词；8 个落地页各拿一个精确词（`/douyin` `/xiaohongshu` `/kuaishou` `/youtube` `/x` `/bilibili` `/gif` `/live-photo`），每页独立标题、描述、H1、三段独有正文与专属问答
- 7 篇教程对准长尾词（`/guides`），真能照着做的步骤，带 HowTo + Article + FAQPage + 面包屑，和落地页互链
- 标题 ≤ 30 汉字、描述 ≤ 80 汉字、每页一个 H1；sitemap（带 lastmod）、robots、canonical、OG 大图、百度 `applicable-device`；服务端渲染、gzip、静态资源内容哈希缓存一年

部署后的清单：设置 `PARSE_VIDEO_SITE_URL`；在百度 / Google / Bing 站长平台验证站点并提交 sitemap；运行 `scripts/push_urls.sh` 主动推送；国内服务器需要 ICP 备案；教程内容可分发到其它平台并链接回站点。

## 🗺️ 路线图

- [x] 35 个平台解析（alcedo）
- [x] GIF / iPhone 实况 / 安卓动态照片
- [x] 小红书、抖音实况图原样打包
- [x] 公网加固：限流、配额、签名、SSRF、CSP
- [x] 落地页 + 教程 + 结构化数据
- [x] Rust 重写：单个二进制，B站 解析 1.1 s → 0.16 s，内存 83 MB → 18 MB
- [ ] iOS 快捷指令：解析结果一键存入相册（含实况）
- [ ] 批量：一次解析多个链接 / 整篇图集打包下载
- [ ] 更多平台的实况图（微博、Instagram）
- [ ] 多实例扩容：任务状态外置 + 产物共享存储（两件都要做，只搬状态解决不了产物在本机磁盘的问题）

## 🤝 贡献

欢迎 Issue 和 PR。

```bash
git clone https://github.com/hiyufan/shizhen.git && cd shizhen
cargo run                                   # 开发
cargo test && cargo clippy --all-targets    # 提交前，两者都要干净
```

- **加平台 / 修解析** — 在 [alcedo](https://github.com/hiyufan/alcedo) 里改，这边把 `Cargo.toml` 里 alcedo 的 `rev` 升上去。
- **改文案** — 落地页在 `content/pages.toml`，教程在 `content/guides.toml`，改完重新编译即可；互链写错（指向不存在的教程）启动时就会报错。改完跑 `python scripts/subset_fonts.py --src <NotoSerifCJK OTF 目录>` 重建衬线字体子集。
- **发起出站请求** — 媒体直链一律走 `net::MediaClient`，它带 SSRF 逐跳检查并复用连接池。
- **新增任务** — 在 `jobs/tasks/` 写一个返回 `JobOutput` 的 async 函数，出错用 `?` 往上抛，写到一半的文件包在 `PartialFile` 里。
- 本机有 ffmpeg 时 `cargo test` 会顺带跑真实的 ffmpeg 流程（没有就跳过）。提交前再用真实链接验证：抖音、小红书、X、B站 各一条，三种转换格式各一次。

## 🙏 致谢

- [alcedo](https://github.com/hiyufan/alcedo) — 视频平台解析
- [parse-video-py](https://github.com/wujunwei928/parse-video-py) — 最早的 Python 版就建在它之上
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — B站 游客高清参数等做法的参考
- [FFmpeg](https://ffmpeg.org/) — 所有转换
- [axum](https://github.com/tokio-rs/axum) · [reqwest](https://github.com/seanmonstar/reqwest) · [minijinja](https://github.com/mitsuhiko/minijinja) · [rusqlite](https://github.com/rusqlite/rusqlite) · [fontTools](https://github.com/fonttools/fonttools)
- 字体：[Noto Serif SC](https://fonts.google.com/noto/specimen/Noto+Serif+SC)、[Inter](https://rsms.me/inter/)、[Playfair Display](https://github.com/clauseggers/Playfair)、[JetBrains Mono](https://www.jetbrains.com/lp/mono/)

## ⚖️ 免责声明与许可

本项目仅供个人学习与研究，请尊重内容创作者的版权，不要用于任何侵权用途。使用本项目部署的服务由部署者自行承担责任。

[MIT License](LICENSE) © 2026 hiyufan，包含上游 parse-video-py 的版权声明。

<div align="center">
<br>
<sub>如果这个项目对你有用，点个 ⭐ 是最好的鼓励</sub>
</div>
