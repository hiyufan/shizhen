<div align="center">

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/logo.svg">
  <source type="image/svg+xml" srcset="docs/logo-animated.svg">
  <img src="docs/logo-animated.gif" width="72" height="72" alt="求原图">
</picture>

# 求原图

**分享链接解析、原图下载、GIF 与实况照片转换，一个网页完成。**

粘贴抖音、小红书、YouTube、X、B站等平台的分享链接，保存视频和原图，或截取一段做成动图。也支持上传本地视频。

[![CI](https://github.com/hiyufan/shizhen/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/hiyufan/shizhen/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-6BAFDF.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-1a1815.svg)](LICENSE)

[简体中文](README.md) · [English](README.en.md)

**[在线体验](https://ynvan.com)** · [快速开始](#快速开始) · [文档](#文档) · [反馈问题](https://github.com/hiyufan/shizhen/issues)

</div>

<p align="center">
  <img src="docs/demo.webp" width="100%" alt="演示：粘贴 YouTube 链接解析，再截 4 秒做成 GIF">
  <br>
  <sub>粘贴链接 → 预览原始素材 → 下载，或选一段转成 GIF。</sub>
</p>

## 功能

- **下载后直接转换**：解析素材，在缩略图条上选一段，生成 GIF、iPhone 实况照片或安卓动态照片。
- **保留原始素材**：提取平台提供的无水印视频码流和原始尺寸图片；支持的平台实况可原样打包，无需重新编码。
- **本地视频也能用**：上传 ≤300 MB 的视频，自选片段、播放速度和封面帧。
- **可以自己部署**：MIT 开源，提供网页、HTTP API 和 Docker Compose 部署方式。

## 支持的平台

支持 30 多个平台，具体格式与清晰度以来源提供的资源为准：

| 素材 | 平台 |
| --- | --- |
| 视频与图片 | 抖音、小红书、快手、YouTube、X、B站、Instagram 等 |
| 静态与动态封面 | [QQ 音乐](https://ynvan.com/qq-music-cover)、[网易云音乐](https://ynvan.com/netease-music-cover)、[Apple Music](https://ynvan.com/apple-music-cover) |
| 游戏素材 | [Steam 动态头像、头像框与背景](https://ynvan.com/steam-assets)、[HoYoPlay 启动器壁纸](https://ynvan.com/hoyoplay-wallpaper) |

支持的视频、图集、实况类型与清晰度见[完整平台表](docs/usage.md#支持的平台)。音乐工具只提取封面。

## 快速开始

### 在线使用

打开 **[ynvan.com](https://ynvan.com)**，粘贴分享链接，点击「解析」后下载素材，或选一段进行转换。使用本站无需注册账号，具体操作见[教程](https://ynvan.com/guides)。

### 本地运行

需要 **Python 3.10+** 和 Git。

```bash
git clone https://github.com/hiyufan/shizhen.git
cd shizhen
./start.sh
```

Windows PowerShell 将 `./start.sh` 换成 `.\start.ps1`。首次运行会创建 `.venv` 并安装依赖，启动后访问 **<http://127.0.0.1:8000>**。FFmpeg 优先使用系统版本，未安装时使用 `imageio-ffmpeg` 自带的版本。

<details>
<summary>手动安装 / 单容器 Docker</summary>

手动安装（macOS / Linux）：

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[web,cli]"
.venv/bin/python main.py
```

Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。

单容器 Docker：

```bash
docker build -t shizhen .
docker run -d -p 8000:8000 -v ./data:/app/data shizhen
```

</details>

## 生产部署

按上面的命令克隆并进入仓库，域名解析到服务器后运行：

```bash
DOMAIN=your.domain docker compose up -d
```

Compose 包含应用、Caddy 自动 HTTPS、YouTube PO Token 服务与 WARP 备用出口。代理、Cookie、配额与部署要求见[自部署与配置](docs/self-hosting.md)。

## 常见问题

<details>
<summary>网页能直接把实况照片存进 iPhone 相册吗？</summary>

网页导出的是配对的 JPG + MOV 文件，不能直接写入 iPhone 相册的 LIVE 格式。可通过 Mac「照片」导入配对文件，或先保存视频再用手机 App 转实况。安卓动态照片需兼容相册支持。

</details>

<details>
<summary>为什么支持的平台也会解析失败？</summary>

分享链接可能过期，内容可能需要登录，平台也可能限制服务器出口。可以先查看[平台状态](https://ynvan.com/status)、重新复制链接；自行部署时，再按[部署排障](docs/self-hosting.md#生产部署)检查代理与 Cookie。

</details>

<details>
<summary>基础 Python 安装包含完整的 YouTube 支持吗？</summary>

完整支持还需要 EJS、JavaScript 运行时和可用出口。Docker 镜像包含额外依赖与下载兜底；手动安装方式及限制见[自部署说明](docs/self-hosting.md#生产部署)和 [YouTube 排障](docs/youtube.md)。

</details>

## 文档

| 要做什么 | 看这里 |
| --- | --- |
| 查平台、格式与封面工具用法 | [使用说明](docs/usage.md) |
| 配置服务器、代理与限流 | [自部署与配置](docs/self-hosting.md) · [自动部署](docs/deploy.md) · [ESA 中继](docs/esa-relay.md) |
| 排查 YouTube 风控与下载失败 | [YouTube 排障](docs/youtube.md) |
| 接入 API 或参与开发 | [开发与 API](docs/development.md) |
| 运行独立的抖音账号助手 | [账号助手说明](apps/douyin-inbox/README.md) |

## 贡献

遇到问题或有功能建议，可以先[搜索已有 Issue](https://github.com/hiyufan/shizhen/issues)，或[提交新 Issue](https://github.com/hiyufan/shizhen/issues/new)。报错时请附可复现的公开链接、预期结果、错误提示和部署方式。

欢迎改进解析器、补充测试、翻译与文档。从[贡献指南](CONTRIBUTING.md)开始即可。

如果求原图帮你省了时间，欢迎点个 Star，或分享给同样需要的人。

## 抖音账号助手

账号助手在同一仓库的 [apps/douyin-inbox/](apps/douyin-inbox/) 独立管理和运行，复用求原图的解析与转换能力。
用户把作品、评论图片或表情包转发给普通抖音账号，助手在同一私信中回传。下方完整说明自动同步自子项目 README。

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

<details>
<summary>致谢</summary>

- [parse-video-py](https://github.com/wujunwei928/parse-video-py)：项目最早从它起步，一些小平台的解析接口至今沿用它找到的
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：YouTube 与兜底解析
- [FFmpeg](https://ffmpeg.org/) / [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)：所有转换
- [FastAPI](https://fastapi.tiangolo.com/) · [httpx](https://www.python-httpx.org/) · [piexif](https://github.com/hMatoba/Piexif)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) · [fxtwitter](https://github.com/FixTweet/FxTwitter)
- 字体：[Noto Serif SC](https://fonts.google.com/noto/specimen/Noto+Serif+SC)、[Inter](https://rsms.me/inter/)、[Playfair Display](https://github.com/clauseggers/Playfair)、[JetBrains Mono](https://www.jetbrains.com/lp/mono/)
- 开头演示动画里的视频：[Big Buck Bunny](https://peach.blender.org/) © Blender Foundation，[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)

</details>

## 免责声明与许可

本项目仅供个人学习与研究，请尊重内容创作者的版权，不要用于任何侵权用途。使用本项目部署的服务由部署者自行承担责任。

[MIT License](LICENSE) © 2026 hiyufan，包含上游 parse-video-py 的版权声明。

原名「拾帧」，2026 年 10 月更名为「求原图」，仓库地址仍为 `hiyufan/shizhen`。包名兼容与旧版升级见[开发说明](docs/development.md#包名与旧版升级)。
