<div align="center">

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/logo.svg">
  <source type="image/svg+xml" srcset="docs/logo-animated.svg">
  <img src="docs/logo-animated.gif" width="72" height="72" alt="求原图">
</picture>

# 求原图

[简体中文](README.md) · [English](README.en.md)

**粘贴一个链接，取出无水印视频和原图，一键做成 GIF、iPhone 实况照片或安卓动态照片。**

支持抖音、小红书、快手、YouTube、X、B站等 30 多个平台。自部署，免费，不用登录。

**也能提取 [QQ 音乐封面](https://ynvan.com/qq-music-cover)和[网易云音乐封面](https://ynvan.com/netease-music-cover)：歌曲、专辑、歌单链接直接贴，动态封面保留动效，还能转 GIF 与实况照片。**

**新增 [Steam 动态头像与头像框](https://ynvan.com/steam-assets)、[HoYoPlay 壁纸](https://ynvan.com/hoyoplay-wallpaper)和 [Apple Music 动态封面](https://ynvan.com/apple-music-cover)，下载与转换在同一个工具完成。**

<sub>原名「拾帧」，2026 年 10 月更名为「求原图」。仓库地址不变。</sub>

[![License](https://img.shields.io/badge/license-MIT-1a1815.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-6BAFDF.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-4DB899.svg)](https://fastapi.tiangolo.com/)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-auto--update-E8702A.svg)](https://github.com/yt-dlp/yt-dlp)
[![Docker](https://img.shields.io/badge/docker-compose%20ready-1a1815.svg)](docker-compose.yml)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-6BAFDF.svg)](#贡献)

### [🌐 在线体验 ynvan.com](https://ynvan.com)

[快速开始](#快速开始) · [功能](#功能) · [账号助手](#抖音账号助手) · [部署](#生产部署) · [性能](#性能) · [配置](#配置) · [API](#api) · [架构](#架构) · [安全](#安全) · [贡献](#贡献)

</div>

<br>

<p align="center">
  <img src="docs/demo.webp" width="100%" alt="演示：粘贴 YouTube 链接解析，再截 4 秒做成 GIF">
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

## QQ 音乐与网易云音乐封面下载

复制歌曲、专辑或歌单的分享链接，即可在线提取对应封面。**歌单取歌单本身的封面，不是第一首歌曲的专辑图。** 不用安装插件，也不用在本站登录。

| 需求 | 在线入口与结果 |
|---|---|
| QQ 音乐歌曲 / 专辑 / 歌单封面 | [QQ 音乐封面提取](https://ynvan.com/qq-music-cover)：公开歌曲与专辑封面可取 800px，歌单封面可取 1000px，以实际资源为准 |
| 网易云音乐封面原图 | [网易云音乐封面下载](https://ynvan.com/netease-music-cover)：按平台提供的原始尺寸保存 |
| 动态歌单封面 | 下载原始 WebP / GIF，保留动效；即使地址以 `.jpg` 结尾，也按真实文件类型保存 |
| 动态封面转 GIF / 实况 | 解析后直接转换；支持 GIF、iPhone 实况包和安卓动态照片，见[操作教程](https://ynvan.com/guide/music-cover-download) |

例如，把 `https://music.163.com/playlist?id=14005122656` 粘贴到工具，即可提取这张歌单的动态封面，再转成 GIF。静态封面直接保存原图，不会伪造动态效果；本功能不下载歌曲音频或批量获取歌单内每首歌曲的封面。

iPhone 网页不能直接把配对文件存成相册实况：用 Mac「照片」导入 JPG + MOV，或先保存视频再用手机 App 转实况。安卓动态照片需兼容相册支持。

## Steam 动态素材、HoYoPlay 壁纸与 Apple Music 封面

| 需求 | 在线入口与结果 |
|---|---|
| Steam 动态头像、透明头像框和贴纸 | [Steam 动态素材](https://ynvan.com/steam-assets)：粘贴带 `/reward/` 的单个物品链接，保留 GIF / APNG，或转成透明 GIF；[教程](https://ynvan.com/guide/steam-animated-assets) |
| Steam 背景视频 | 同一工具下载物品提供的 MP4 / WebM，也能截取片段做 GIF 或实况 |
| 原神、星穹铁道、绝区零、崩坏 3 启动器背景 | [HoYoPlay 壁纸](https://ynvan.com/hoyoplay-wallpaper)：选择游戏，保存当前国服公开壁纸与可用动态视频；[教程](https://ynvan.com/guide/hoyoplay-wallpaper-download) |
| Apple Music 静态与动态封面 | [Apple Music 封面](https://ynvan.com/apple-music-cover)：歌曲、专辑或歌单链接；公开动态封面可存 MP4，再转 GIF / 实况 |

Steam 的大图可能只是静态预览，本项目选择公开的动画文件；`.png` 也可能是 APNG。保存文件不会解锁 Steam 账号内的物品。HoYoPlay 提取当前版本，不提供历史壁纸档案；Apple Music 只提取公开封面，不下载歌曲音频。动态素材是否提供，以平台当前数据为准。

## 为什么做这个

短视频平台把内容锁在 App 里：保存的视频带水印，图片被压缩，实况图只剩静态一帧，想做个表情包还得再找一个工具。求原图把这几步合成一次粘贴：

| 步骤 | 做什么 |
|---|---|
| 解析 | 贴上分享链接，拿到平台给网页端的无水印播放地址和原始尺寸图片 |
| 转换 | 在缩略图条上拖出想要的几秒，直接生成 GIF、实况照片或动态照片 |
| 原样打包 | 小红书 / 抖音自带的实况图，原图和短视频配好对，导入相册就会动 |

解析层基于 [parse-video-py](https://github.com/wujunwei928/parse-video-py) 和 [yt-dlp](https://github.com/yt-dlp/yt-dlp)；本项目把两者整合起来，补上转换能力，并做成一个可以公开部署的网站。

## 功能

<table>
<tr><td width="33%" valign="top">

**提取**

- 无水印视频原始码流
- 小红书原图（常见 1920×2560）
- 抖音 H.265 高清档 + 背景音乐
- YouTube / B站 服务端合并 1080p–4K
- 实况图识别与整篇打包
- QQ 音乐 / 网易云音乐歌曲、专辑与歌单封面提取
- 直链代理，浏览器直接播放不 403
- 失败按「已删除 / 需登录 / 被限流」分类

</td><td width="33%" valign="top">

**转换**

- **GIF** ≤30s，帧率 5–30、宽度 160–960、速度 0.5–3×，两遍调色板编码
- **iPhone 实况** ≤10s，JPG + MOV 配对，`ContentIdentifier` 双向写入并回读校验
- **安卓动态照片** 内嵌 MP4 的 JPG，Motion Photo + MicroVideo XMP
- **原样打包** 平台实况图不重编码，只换封装
- **本地视频** 上传即转，≤300 MB
- **动态音乐封面** 按真实图片格式解码，转 GIF、实况照片或安卓动态照片

</td><td width="33%" valign="top">

**网站**

- 编辑风极简界面，手机端完整适配
- 字体全部自托管，页面零外部请求
- 13 个落地页 + 16 篇教程，JSON-LD 结构化数据
- 公网加固：限流、配额、签名、SSRF、CSP
- Docker 非 root 运行
- yt-dlp 自动升级跟进平台改版
- 站长统计页 `/stats`：每小时 / 每天多少人在用、各平台成功率、失败原因

</td></tr>
</table>

## 抖音账号助手

账号助手在同一仓库的 [apps/douyin-inbox/](apps/douyin-inbox/) 独立管理和运行，复用求原图的解析与转换能力。
以下内容自动同步自其 README。

<!-- BEGIN DOUYIN-INBOX README -->
<details>
<summary><b>查看账号助手的安装、使用与运维说明</b></summary>

### 抖音普通账号助手

用户把作品、评论图片或表情包转发给一个普通抖音账号，助手解析后在同一私信中回传。
视频和完整实况先询问格式，用户选择原格式、GIF 或两者；实况原格式保存到 iPhone 后为 LIVE。

这是 [shizhen / 求原图](README.md) 仓库中的独立应用，包名为 `qiuyuantu-inbox`，
Python 模块为 `qiuyuantu_inbox`。源码、测试、部署模板和操作文档都在本目录管理。
它复用主项目的解析器、安全下载和媒体转换，单独安装、单独运行；主项目 Web 镜像不包含此应用。

#### 能力与边界

| 输入 | 回传与交互 |
| --- | --- |
| 作品图片 / 图集 | 原图逐项回传 |
| 普通短视频作品 | 回复 `1` 取视频、`2` 取 GIF、`3` 两者都要 |
| 完整实况作品 | 回复 `1` 取 LIVE、`2` 取 GIF、`3` 两者都要；照片和视频逐项配对 |
| 评论静态图 / GIF | 提取评论自身素材；GIF 保留动画 |
| 私信相册实况 | 原格式分享保留 LIVE，选择 GIF 后才提取配套视频 |
| 新粉丝接入（可选） | 发现启用后新增的粉丝，排队回关并查询互关结果，再使用现有转发入口 |

多条待选择时按提示回复 `3 编号`，编号可用前 4 位，重号时补全；若先回复了 `GIF` 等格式，
再单独补编号即可，提示会列出各编号对应的作品标题。也可发送 `进度 完整编号`、`取消 完整编号`。
GIF 默认转换每段视频的前 6 秒，最长边 480 像素、12 帧/秒，并限制文件体积。

图片、GIF、普通视频及实况已做真实账号验证，接收方确认 LIVE 可保存、播放。
部分评论卡片只有静态预览，取不到原始视频时会说明原因，不能静默返回静态图冒充 LIVE。
陌生人消息请求入口、普通长视频和推广后的持续高峰容量仍需验证。

#### 安装与启动

以下命令均从 **shizhen 仓库根目录** 执行。使用独立虚拟环境，不复用 Web 服务或开发测试环境：

```bash
python -m venv apps/douyin-inbox/.venv
source apps/douyin-inbox/.venv/bin/activate
python -m pip install -e '.[cli]' -e ./apps/douyin-inbox
python -m playwright install chromium

export PARSE_VIDEO_ACCOUNT_DIR="$HOME/.local/share/qiuyuantu-inbox"
qiuyuantu-account doctor
qiuyuantu-account login --headed --timeout 0
```

扫码、验证码由账号持有人在浏览器完成；无桌面服务器的远程窗口配置见[操作手册](apps/douyin-inbox/docs/operations.md)。
另开终端激活同一个环境，并设置同一个账号目录，先只收件观察，再明确启用回传：

```bash
# 当前会话试运行，不回传
qiuyuantu-inbox run --pilot-current

# 所有可发现的一对一会话，从各自收件基线接收新请求并自动回传
qiuyuantu-inbox run --all --send
```

上述命令须分别运行，不要同时启动两个账号进程。也可使用
`python -m qiuyuantu_inbox` 和 `python -m qiuyuantu_inbox.account`。
不传 `--send` 只收件；开放所有会话需要显式 `--all --send`，无需手动维护用户名单。

#### 运行与配置

```bash
qiuyuantu-inbox status
qiuyuantu-inbox jobs --state waiting
qiuyuantu-inbox jobs --state uncertain
qiuyuantu-inbox pause
qiuyuantu-inbox resolve <任务编号> delivered
qiuyuantu-inbox resume
```

`delivered` 仅用于已经核实送达的消息；放弃未知结果用 `abandon`。未知结果会暂停，不会自动重发。
账号数据由 `PARSE_VIDEO_ACCOUNT_DIR` 指定，包含登录 profile、SQLite 队列、媒体缓存与截图，不能提交或公开托管。
未配置时使用 `$XDG_DATA_HOME/qiuyuantu-inbox`（默认 `~/.local/share/qiuyuantu-inbox`）；
兼容旧的 `PARSE_VIDEO_DATA_DIR/accounts/douyin`，迁移时应明确指定原账号目录，保留去重基线和待选任务。
浏览器调试和远程桌面只监听本机，账号 Cookie 不与匿名解析器共享。

队列使用 SQLite WAL，包含事件落盘与补扫、按用户公平调度、共享素材和有界反压，当前无需 Redis / RabbitMQ。
默认两路准备、一条 GIF 编码、每用户最多 3 条待办、每小时 30 条请求、全局 300 条待办。

自动回关默认关闭，只能与 `--all --send` 一起启用：

```bash
export PARSE_VIDEO_INBOX_AUTO_FOLLOW=true
qiuyuantu-inbox run --all --send
```

首次扫描仅建立现有粉丝基线，完整扫描后才接收新增粉丝，不批量回关历史账号。
默认扫描间隔 30 秒、每轮最多 3 页，单路回关至少间隔 30 秒、每小时 20 次、滚动 24 小时 100 次。
回关队列上限 100；这些是可调的应用预算。`status` 的 `follows` 展示基线、积压、暂停与扫描错误。
`follow-pause` / `follow-resume` 仅控制回关；未知提交始终只查关系、不重发，平台拒绝不会暂停媒体队列。
私密账号的关注申请不等于已互关，需等待对方批准；取关后重关注不会重置已经记录的用户去重状态。
已用未记录过的新号验证自动发现、回关、互关后转发视频、格式选择及视频回传的完整流程。

可选原生流水线通过以下环境变量启用：

```bash
export PARSE_VIDEO_INBOX_NATIVE_PIPELINE=true
export PARSE_VIDEO_INBOX_UPLOAD_WORKERS=2
export PARSE_VIDEO_INBOX_UPLOAD_STREAMS=2
export PARSE_VIDEO_INBOX_ACK_WINDOW=1
```

它把预上传、串行提交和回执观察拆开，复用页面内资源缓存；最多两路文件上传、100 MiB 在途源文件，
发送默认至少间隔 5 秒。单用户按顺序回传，SDK 额外逻辑重试被阻止，未知送达仍要求核对。
等待选择期间可空闲预热原格式；预热与正式上传共用并发和字节预算，正式请求优先。
客户端事件主动唤醒收件，落盘及阶段完成后通知下一阶段，同时保留定时补查与会话补扫。
作品识别优先复用本地素材和客户端已加载详情，命中时无需切换聊天；补扫在会话之间释放界面，减少对提示回传的阻塞。
主进程只保存不含凭证的页面句柄，原生相册分享保留已验证的串行回退路径。
这些是应用预算，不能当作平台频率承诺或多用户容量已通过验收的证明。
全部参数、实现设计及真实验证记录见[操作手册](apps/douyin-inbox/docs/operations.md)。

#### 开发与部署

```text
apps/douyin-inbox/
├── pyproject.toml           独立安装包与两个命令入口
├── src/qiuyuantu_inbox/     浏览器适配、队列、调度与回传
├── tests/                  离线替身、队列和 JS 契约测试
├── deploy/                 systemd 服务与范围 / 流水线覆盖配置
├── docs/operations.md      环境、操作、设计和验收记录
└── scripts/sync_readme.py   同步主 README 的嵌入区块
```

开发测试环境安装主项目后，以 `--no-deps` 安装子项目即可运行离线测试；浏览器依赖保留在账号运行环境：

```bash
.venv/bin/python -m pip install -e '.[web,cli,dev]'
.venv/bin/python -m pip install --no-deps -e ./apps/douyin-inbox
.venv/bin/python -m pytest -q apps/douyin-inbox/tests
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
python apps/douyin-inbox/scripts/sync_readme.py --check
```

离线测试不连接真实账号；平台验证需另行报告。CI 分别检查主项目和账号助手。
部署模板见 [deploy/](apps/douyin-inbox/deploy)，安装前核对解释器与账号目录；代码提交不会自动替你启用 `--send`。
修改本 README 后运行 `python apps/douyin-inbox/scripts/sync_readme.py`，同步主 README 的可折叠内嵌内容。

</details>
<!-- END DOUYIN-INBOX README -->

## 支持的平台

| 平台 | 视频 | 图集 | 实况图 | 清晰度 | 说明 |
|---|:-:|:-:|:-:|---|---|
| 抖音 | ✅ | ✅ | ✅ | 720p – 1080p，H.265 | 背景音乐可单独下载 |
| 小红书 | ✅ | ✅ | ✅ | 最高档 | 链接需带有效 `xsec_token`，现复制现用 |
| 快手 | ✅ | ✅ | – | 平台默认 | |
| YouTube | ✅ | – | – | 可读直链，720p – 4K 服务端合并 | Docker 内置原生会话 / SABR 失败兜底 |
| X (Twitter) | ✅ | ✅ | – | 最高码率 | 敏感推文走 fxtwitter 兜底 |
| B站 | ✅ | ✅ | ✅ | 480p 直链，720p / 1080p 服务端合并 | b23.tv 短链可用；动态里的图和实况也能取；番剧、电影不支持 |
| Instagram | ✅ | ✅ | – | 平台默认 | 多图帖取原图，图片和视频混着的也行 |
| QQ 音乐 | – | 封面 | – | 歌曲 / 专辑 800px，歌单 1000px | 歌曲、专辑、歌单分享链接；只提取封面 |
| 网易云音乐 | – | 封面 | – | 平台提供的原始尺寸 | 歌曲、专辑、歌单分享链接；动态封面可保留原格式或转 GIF / 实况 / 动态照片 |
| Steam 点数商店 | ✅ | GIF / APNG 等素材 | – | 平台提供的原始素材 | 单个物品链接；动态头像、透明头像框、贴纸、MP4 / WebM 背景；动画可转换 |
| HoYoPlay | ✅ | 当前启动器壁纸 | – | 平台提供的原始素材 | 选择四款游戏，读取当前国服公开背景；有视频时可转换，不含历史档案 |
| Apple Music | 动态封面 | 封面 | – | 图片最高请求 3000px；动态视频最高 1080p | 歌曲、专辑、歌单公开封面；动态视频按作品提供情况显示，不下载歌曲音频 |
| 微博、西瓜、皮皮虾、AcFun、TikTok … | ✅ | 部分 | – | | 30+ 平台，其余交给 yt-dlp 兜底 |

> 解析依赖各平台网页结构，平台改版可能暂时失效。上游 parse-video-py 更新较勤，修了某个平台时可以对照着改 `parser/` 里的解析器（不能直接覆盖，见[贡献](#贡献)）。

## 快速开始

需要 **Python 3.10+**。ffmpeg 不用单独装：PATH 里有就用系统的，没有就自动用 `imageio-ffmpeg` 自带的。

```bash
git clone https://github.com/hiyufan/shizhen.git && cd shizhen
./start.sh          # Windows: .\start.ps1
```

首次运行会自动创建 `.venv` 并安装依赖，然后打开 <http://127.0.0.1:8000>。

Python 包、安装包和命令行入口统一为 `qiuyuantu`，例如 `from qiuyuantu import parse_video_share_url`、`qiuyuantu version`。命令行功能需安装 `cli` extra。旧的 `parse_video_py` 导入（含子模块）、`parse-video-py` 命令和 `python -m parse_video_py.diag` 继续兼容；HTTP 接口及 `PARSE_VIDEO_*` 环境变量保持原有用法。

源码部署从旧版本升级时，先在原虚拟环境中运行 `python -m pip uninstall parse-video-py`，再按下方命令安装当前仓库，避免两个发行包同时提供旧入口。Docker 部署重新构建镜像即可。仓库地址仍为 `hiyufan/shizhen`。

<details>
<summary>手动安装 / 单容器 Docker</summary>

```bash
# 手动
python -m venv .venv
.venv/bin/pip install -e ".[web,cli]"      # Windows: .venv\Scripts\pip
.venv/bin/python main.py

# Docker
docker build -t shizhen .
docker run -d -p 8000:8000 -v ./data:/app/data shizhen
```

</details>

## 生产部署

一台 **4 核 / 8 GB / 100 Mbps** 的 VPS 就能跑。`docker-compose.yml` 带了四个服务：应用、[Caddy](https://caddyserver.com/)（自动 HTTPS 反代）、[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)（YouTube PO Token）和 WARP 备用出口。

```bash
# 域名先解析到这台机器
DOMAIN=your.domain PARSE_VIDEO_SITE_URL=https://your.domain docker compose up -d
```

不用 Docker 的话，自己起 Nginx / Caddy 反代到 `127.0.0.1:8000`，并设置 `PARSE_VIDEO_TRUST_PROXY=1` 让限流拿到真实客户端 IP。

排查问题：`docker compose exec app python -m qiuyuantu.diag "<分享链接>"`，会打印出口 IP、解析结果和平台返回的原始状态。

push 到 main、测试通过后自动上线，自检不过自动回滚：配置方法见 [docs/deploy.md](docs/deploy.md)。

<details>
<summary><b>海外服务器必看</b>：小红书 / B站 会拒绝海外和机房 IP</summary>

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

把 `scripts/esa-relay.js` 部署到阿里云 ESA 边缘函数（改掉里面的 `TOKEN`），国内平台的解析请求就从国内边缘节点发出：

```bash
PARSE_VIDEO_RELAY_CN=https://<函数域名>/relay
PARSE_VIDEO_RELAY_TOKEN=<同一个 TOKEN>
```

可以再开 `PARSE_VIDEO_EDGE_IMG=1` / `PARSE_VIDEO_EDGE_MEDIA=1`，让国内用户直接从边缘节点取图片和视频，不用绕海外服务器。局限：yt-dlp 走不了中继，B站 只有 480p 直链。部署、探测出口和口令的注意事项见 [docs/esa-relay.md](docs/esa-relay.md)。

**3. 贴登录 Cookie**

```bash
PARSE_VIDEO_XHS_COOKIE="a1=...; web_session=..."
PARSE_VIDEO_BILI_COOKIE="SESSDATA=...; buvid3=..."
```

F12 → Network → 请求头里的 Cookie。B站 不登录也会自动领一份 buvid 设备指纹，多数情况已够。抖音的图文（note/slides）不用配 cookie：平台对匿名 API 收紧后，解析会自动起一个一次性无痕 Chromium 渲染 PC 版页面兜底（全程无账号）；可选再配 `PARSE_VIDEO_DOUYIN_COOKIE`，让图文走 slidesinfo 拿到全量数据（含实况视频），不配只是实况图在这条兜底路上拿不到。

**4. 部署在国内**，再用 `PARSE_VIDEO_PROXY` 给 YouTube 配海外出口。

</details>

<details>
<summary><b>风控</b>：YouTube 提示「确认不是机器人」</summary>

<br>

完整的 YouTube 支持需要 EJS 脚本、JS 运行时和可用出口。Docker 已装 Deno；手动安装用 `pip install -e ".[web,cli,youtube]"`。`/api/health` 的 `js_runtime` 应为 `deno` 或受支持的 `node`，不能是 `null`。

compose 已配好 `PARSE_VIDEO_POT_URL=http://bgutil:4416`，插件与服务端固定为相同版本。升级时要一起更新，不要只拉一个 `latest`。

Docker 还包含独立解释器中的原生 Chromium 会话 / SABR 兜底。普通解析失败，或返回的媒体地址不可读时，会用同一浏览器会话生成内容绑定 Token，通过现有 WARP 完整下载、校验并缓存视频，随后复用预览、下载和转换。已在两个此前触发机器人验证的公开视频上验证完整下载，不需要账号 Cookie。高分辨率和音频下载失败时也可调用这条路径。

同一链接的并发解析会合并为一次抓取；已验证的 YouTube 视频按视频 ID 复用缓存，长链接、短链接和时间戳分享都能命中，缓存读取无需等待其它视频的浏览器任务。默认路径失败后，备用解析和原生兜底并行恢复，接受先完成且通过媒体验证的结果。

PO Token 不保证解除 IP 风控。其它部署可配置 `PARSE_VIDEO_PROXY_YOUTUBE`，只让 YouTube 的解析和媒体走这个出口；需要登录的内容仍需有效的 `cookies.txt`。自检会检查两条视频并读取媒体片段，单条解析成功不会把整个平台报成正常。

实测结果、限制与排查方法见 [YouTube 排障](docs/youtube.md)。

`PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS=7` 会每周升级 yt-dlp 并在空闲时原地重启，平台改版全靠它跟进。

</details>

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

## 配置

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
| `PARSE_VIDEO_MCP` | `1` | 设 `0` 关闭 `/mcp`（Docker 镜像默认关） |

</details>

<details>
<summary><b>解析</b></summary>

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

</details>

<details>
<summary><b>配额与限流</b></summary>

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
| `PARSE_VIDEO_STATS_IGNORE_IPS` | – | 不计入统计的 IP（逗号分隔），填服务器自己的公网地址；本机 / 内网地址本来就不计。站长自己的设备打开 `/test` 用统计口令开一次测试模式 |
| `PARSE_VIDEO_FEEDBACK_REPO` / `_GH_TOKEN` / `_KEY` | – | 解析失败反馈：建 issue 的私有仓库、只给它 Issues 读写的 fine-grained token、加密反馈邮箱的 32 字节密钥（base64）。和下面的 SMTP 四样都填了才显示「反馈这个问题」 |
| `PARSE_VIDEO_SMTP_USER` / `_PASS` / `_HOST` / `_PORT` | – / – / `smtp.qq.com` / `465` | 反馈修好后发通知邮件的发件邮箱和 SMTP 授权码 |
| `PARSE_VIDEO_ALERT_TO` | – | 站长收告警的邮箱（用上面的 SMTP 发）。每小时自检后判断：一半以上平台连续两轮超时 / 连不上（多半是服务器自己断网了），或某个平台连续三轮解析失败（多半改版了），就发一封；恢复了再发一封，同一个问题不重复发 |
| `PARSE_VIDEO_STATS_DAYS` | `90` | 明细保留天数 |

数据在 `data/stats.db`（SQLite）。只记事件：成功的解析不记链接，IP 经密钥 HMAC 后只留 12 位，能数出人数还原不出是谁。解析失败的链接另存在 `data/failures.db`，只留 7 天，统计页上列出来方便排查（站长测试设备和服务器自己的请求不记）。`/api/stats?range=7d&token=…` 直接拿 JSON。

</details>

## API

网页用的就是这几个接口，快捷指令、脚本、自己的 App 都能接。上游 parse-video-py 的 `/video/share/url/parse`、`/video/id/parse`、`/mcp` 原样保留。

```bash
curl -s "https://ynvan.com/api/parse?url=https://v.douyin.com/8x0AeP8prW8/"
```

```jsonc
{
  "code": 200,
  "data": {
    "source": "douyin",
    "title": "济公爷爷为大家祈福啦…",
    "author": { "name": "济公爷爷·游本昌" },
    "duration": 49.5, "width": 720, "height": 1280,
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
| `GET` | `/api/parse?url=` | 解析分享文字 / 链接。失败时返回 `reason`：`deleted` `login` `blocked` `network` `timeout` `unsupported` `empty` `parse` |
| `GET` | `/api/parse/stream?url=` | 同一次解析实时发送阶段事件，最后发送与 JSON 接口相同的结果；网页据此显示轻松的阶段提示、等待时间和备用方式标记 |
| `GET` | `/api/proxy?url=&sig=&filename=&download=1` | 转发直链，支持 Range；只接受解析结果里签过名的地址 |
| `POST` | `/api/prepare` | `{url \| page_url, sig, title}` 把原视频缓存到服务端，返回 `source_id` 或后台任务 |
| `POST` | `/api/upload` | multipart 上传本地视频，返回 `source_id` |
| `GET` | `/api/source/{id}` · `/strip?n=` | 原视频（可拖动播放）· 缩略图条 |
| `POST` | `/api/convert` | `{source_id, format: gif\|livephoto\|motionphoto, start, end, fps, width, dither, speed, key_time}` |
| `POST` | `/api/live` | `{items: [{image_url, video_url, image_sig, video_sig}], format, title}` 平台实况图原样打包 |
| `POST` | `/api/download` | `{page_url, format_spec, sig, title}` 服务端用 yt-dlp 合并下载 |
| `GET` | `/api/jobs/{id}` · `/file` · `/preview` · `/video` | 任务进度 · 结果文件 · 实况封面 · 实况 MOV |
| `DELETE` | `/api/jobs/{id}` | 取消任务（仅发起者） |
| `GET` | `/api/health` | 队列、磁盘、yt-dlp 版本 |

解析状态流为 SSE：`data` 中的 `{type: "progress", stage, fallback}` 表示真实阶段，`{type: "result", result}` 是最终 JSON 结果。网页保留轻松的文案，显示实际等待时间，并在进入兜底后持续标记「备用方式已启用」；缓存命中不闪等待条，清空或换链接会停止旧请求和计时。阶段事件不传 Cookie、PO Token 或内部服务信息，原 JSON 解析接口继续可用。

任务接口都返回 `{id, status: queued|running|done|error, progress, message, filename, filesize}`，轮询 `/api/jobs/{id}` 直到 `done` 后取 `/file`。限流触发时返回 `429` 并带 `Retry-After`。

## 架构

```mermaid
flowchart LR
    U[浏览器 / 脚本] -->|/api/parse| P[解析层]
    P --> A[parse-video-py 解析器<br/>抖音 · 小红书 · 快手 · X · B站 …]
    P --> Y[yt-dlp<br/>YouTube · TikTok · 兜底]
    P -->|签名后的地址| U
    U -->|/api/proxy| X[直链代理<br/>Referer · Range · SSRF 检查]
    U -->|/api/prepare · convert · live| J[任务队列<br/>限流 · 配额 · 超时]
    J --> F[ffmpeg<br/>GIF · MOV · 抽帧]
    J --> L[实况元数据<br/>Apple ContentIdentifier · Motion Photo XMP]
    J -->|/api/jobs| U
```

```
apps/douyin-inbox/               独立抖音账号助手：源码、测试、部署与文档
main.py                          启动入口
src/parse_video_py/               旧包名兼容入口，按需转发到 qiuyuantu
src/qiuyuantu/
  parser/                        上游解析器（抖音 / 小红书 / 快手 / X / B站 …），本项目增强了 douyin / redbook / twitter
  parser/ytdlp.py                yt-dlp 通用解析器，清晰度列表
  parser/errors.py               解析错误分类
  convert/ffmpeg.py              ffmpeg 封装：GIF、切段编码、抽帧、缩略图条、进度
  convert/livephoto.py           Apple MakerNote / MOV 标识、Motion Photo XMP
  convert/tasks.py               后台任务：准备原视频、转 GIF / 实况 / 动态照片、实况打包
  convert/fetch.py               下载：直链流式下载、国内 CDN 走中继兜底、yt-dlp 下载合并
  convert/bilibili.py            B 站高清与仅音频下载：挑选 DASH 分轨并合并
  convert/jobs.py · store.py     任务队列、原视频缓存、磁盘配额
  convert/net.py                 SSRF 防护、链接签名、出站连接池
  convert/relay.py               边缘函数中继 transport
  convert/updater.py             yt-dlp 自动升级
  diag.py                        站长诊断：python -m qiuyuantu.diag <链接>
  web/app.py                     组装 FastAPI：后台任务启停、中间件、路由
  web/legacy_api.py              上游兼容接口，保留原有请求路径与返回格式
  web/limits.py                  按 IP 限流与并发配额
  web/pages.py · parse.py        页面与 SEO 落地页；/api/parse 解析、/api/feedback 失败反馈
  web/proxy.py · media.py        /api/proxy 直链转发；原视频准备、转换、下载、任务查询
  web/admin.py · middleware.py   健康检查、统计、测试模式；安全响应头与浏览统计
  feedback.py                    解析失败反馈：GitHub issue、加密邮箱、修好后复测再发邮件
  stats.py                       使用统计：SQLite 事件表、按时间分桶汇总，/stats 页面
  seo.py · guides.py             落地页与教程内容、JSON-LD、sitemap
  templates/                     base / index / guide / guides / stats / 404
  static/                        自托管字体（衬线按站内用字子集化）、样式、OG 图
  static/js/                     前端 ES 模块：app（首页入口）· result · converter · trimmer · save · api · ui · dom，
                                 site（全站动效）、stats（统计页）。整目录按内容哈希挂在 /js/<版本>/ 下
scripts/push_urls.py             百度主动推送 + sitemap ping
scripts/esa-relay.js             阿里云 ESA 边缘函数：/probe 探测出口，/relay 给海外服务器当国内中继
scripts/subset_fonts.py          改文案后重建衬线字体子集
docker/youtube/worker.py         独立解释器中的 YouTube 原生会话下载与媒体验证
docker-compose.yml · Caddyfile   一台机器的 HTTPS 部署
```

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

## 路线图

- [x] 30+ 平台解析，yt-dlp 兜底
- [x] GIF / iPhone 实况 / 安卓动态照片
- [x] 小红书、抖音实况图原样打包
- [x] 公网加固：限流、配额、签名、SSRF、CSP
- [x] 落地页 + 教程 + 结构化数据
- [x] 出站连接池复用，解析与下载提速 66% / 69%
- [ ] iOS 快捷指令：解析结果一键存入相册（含实况）
- [ ] 批量：一次解析多个链接 / 整篇图集打包下载
- [ ] 更多平台的实况图（微博、Instagram）
- [ ] 多实例扩容：任务状态外置 + 产物共享存储（两件都要做，只搬状态解决不了产物在本机磁盘的问题）
- [ ] 把抖音多档清晰度、小红书原图、X 兜底回馈给上游 parse-video-py

## 贡献

欢迎 Issue 和 PR。

```bash
git clone https://github.com/hiyufan/shizhen.git && cd shizhen
python -m venv .venv && .venv/bin/pip install -e ".[web,cli,dev]"
.venv/bin/python main.py
```

- **规范**：Python 用 ruff 格式化和检查（规则在 `pyproject.toml`：行宽 120、圈复杂度 ≤ 10、import 排序、常见 bug 写法），前端脚本用 ESLint（`eslint.config.mjs`：复杂度 ≤ 10、嵌套 ≤ 4 层、函数 ≤ 80 行），CI 里任何一项不过就不部署。`pre-commit install` 后每次提交自动跑 ruff；前端提交前跑 `npx eslint@10 src/qiuyuantu/static/js`。测试：`python -m pytest -q`，不碰外网。
- **分层**：`parser/` 只管「链接 → VideoInfo」，不碰 HTTP 接口；`convert/` 是下载、转换、任务队列，不认识 FastAPI；`web/` 只做参数校验、鉴权、限流，把活交给前两层。前端脚本在 `static/js/`，按功能分模块，不往模板里写内联脚本。
- **加平台**：在 `src/qiuyuantu/parser/` 写一个 `BaseParser` 子类，在 `parser/__init__.py` 注册域名。返回 `VideoInfo`，直链多档清晰度放 `formats`（带 `url`），需要服务端合并的放 `format_spec`。
- **改文案**：落地页在 `seo.py`，教程在 `guides.py`。改完跑 `python scripts/subset_fonts.py --src <NotoSerifCJK OTF 目录>` 重建衬线字体子集（900 字重只收各页大标题，`tests/test_fonts.py` 会查缺字）。字体 URL 自动带内容哈希，不用管缓存。
- **同步上游**：`parser/` 里的解析器都已按本项目的写法重写过（统一用 `BaseParser.get_json / get_text` 发请求、失败抛带原因的 `ParseError`），不能再拿上游文件直接覆盖；上游修了某个平台时对照着改，并补上 `tests/test_small_parsers.py` 里的离线用例。
- **发起出站请求**：一律用 `utils.create_async_client()` 或 `convert.net.safe_client()`，它们带 SSRF 逐跳检查并复用连接池。不要直接 `httpx.AsyncClient()`。
- **普通账号工作流**：独立应用见 [apps/douyin-inbox/README.md](apps/douyin-inbox/README.md)，源码、测试、部署与运维文档都在该目录；主包与 Web 镜像不包含账号助手。
- 提交前用真实链接验证：抖音、小红书、X、B站 各一条，三种转换格式各一次。

## 致谢

- [parse-video-py](https://github.com/wujunwei928/parse-video-py)：项目最早从它起步，一些小平台的解析接口至今沿用它找到的
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：YouTube 与兜底解析
- [FFmpeg](https://ffmpeg.org/) / [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)：所有转换
- [FastAPI](https://fastapi.tiangolo.com/) · [httpx](https://www.python-httpx.org/) · [piexif](https://github.com/hMatoba/Piexif)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) · [fxtwitter](https://github.com/FixTweet/FxTwitter)
- 字体：[Noto Serif SC](https://fonts.google.com/noto/specimen/Noto+Serif+SC)、[Inter](https://rsms.me/inter/)、[Playfair Display](https://github.com/clauseggers/Playfair)、[JetBrains Mono](https://www.jetbrains.com/lp/mono/)
- 开头演示动画里的视频：[Big Buck Bunny](https://peach.blender.org/) © Blender Foundation，[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)

## 免责声明与许可

本项目仅供个人学习与研究，请尊重内容创作者的版权，不要用于任何侵权用途。使用本项目部署的服务由部署者自行承担责任。

[MIT License](LICENSE) © 2026 hiyufan，包含上游 parse-video-py 的版权声明。

<div align="center">
<br>
<sub>如果这个项目对你有用，点个 ⭐ 是最好的鼓励</sub>
</div>
