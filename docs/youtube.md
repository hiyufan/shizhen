# YouTube 解析与下载排障

2026-10-21 在当前服务器实测。YouTube 的可用性会随出口、视频和平台策略变化，这份记录不能保证所有公开视频永久可下载。

**最终结果：两条此前被机器人验证拦截的公开视频，均已用原生 Chromium 会话 + 内容绑定 Token + 实际播放器 STS + SABR + 同一 WARP 出口完整下载并解码验证。该方法已接入应用失败兜底。以下保留各轮结果，早期失败结论不代表第三轮的最终结果。** 本次构建与验证使用隔离容器，没有重启或替换线上服务。

## 这次确认的问题

1. 应用容器安装了 `yt-dlp-ejs==0.8.0`，却没有 Deno / Node。yt-dlp 明确警告缺少 JS 运行时；由于调用参数关闭了警告，页面和健康检查没有显示这个缺口。现在 `youtube` 安装组带上 Deno，解析和合并下载都会显式传入运行时路径，直接用虚拟环境里的 Python 启动也能找到它。
2. Python PO Token 插件为 2.0.2，HTTP 服务实际为 2.0.0。镜像标签 `latest` 不会自动更新已经运行的容器。现在插件和 compose 服务固定为同一版本 2.0.2，后续一起更新。
3. 原自检只解析一条公开视频，不能发现媒体 403 或其它视频的风控。现在检查两条有效样本，并请求 `Range: bytes=0-1023`，拿到非空媒体才算成功。补充样本失效时会跳过，不把删除的内容算作平台故障。
4. 解析通过备用出口成功后，后续合并下载原来还会先试默认出口。现在连同原页面一起记住成功出口，下载和媒体代理复用它；记录过期后释放。
5. “Sign in to confirm you're not a bot” 原来被通用规则归到登录。现在归为 `blocked`；年龄确认仍归为 `login`。YouTube 下载时的 HTTP 403 / 429 也允许一次备用出口重试，但不会在已失败的同一个备用出口上重复尝试。
6. YouTube 失败时没有成功结果中的平台字段，以前会被统计到通用的 `ytdlp`。现在解析失败与失败缓存命中都记到 `youtube`，不会再因为分类问题漏看失败。历史统计未改写。

## 实测结果与边界

第一轮使用独立诊断进程、临时 Deno 和临时 PO Token 服务，没有替换生产进程或写入用户统计。第一轮媒体验证每条分轨只读取 1 KB；第三轮改为完整下载和全文件解码。

| 条件 | 结果 |
|---|---|
| yt-dlp 2026.08.19 + Deno 2.9.8 + 插件 / 服务 2.0.2，`dQw4w9WgXcQ` | 默认客户端的 360p 合流及 2160p 分轨均返回 HTTP 206 和实际媒体字节 |
| 同环境强制 `mweb`，同一视频 | 能列出清晰度，但所测媒体返回 403；因此没有强制切换所有请求到 mweb |
| `iN1N8o3Miv4`，默认出口 / WARP / mweb | 仍要求确认不是机器人；WARP 的网页请求还出现过 429 |
| 临时测试上游 nightly 2026.10.10.233141 + Deno，同一失败视频 | 仍要求确认不是机器人，没有据此把生产依赖切到 nightly |
| 第二条公开视频 `YE7VzlLtp-4`，默认出口 | 同样触发机器人验证，说明单条固定自检通过不足以代表出口可用 |
| 新建应用镜像，Token 服务 2.0.2，`dQw4w9WgXcQ` | 720p 音视频完整下载合并为 21,082,307 字节 MP4；探测为 1280×720、有音频、213.09 秒；再转 2 秒 GIF 成功 |

补齐 JS 与 PO Token 能解决依赖不完整和部分媒体请求的问题，不能保证解除平台对出口或内容的访问限制。重试、切客户端和升级版本都有边界。

### 第二轮：替代实现与浏览器验证

同日继续测试，所有实验运行在独立临时容器或诊断进程中，没有安装到线上服务。目标为 `iN1N8o3Miv4`，对照为 `dQw4w9WgXcQ`；除 IPv6 测试外使用服务器现有默认出口，不使用账号 Cookie。

| 路径 | 验证到哪一步 | 结果 |
|---|---|---|
| Chromium 156 原生网页播放器，新建匿名会话 | 同一会话中对照视频真实播放约 2 秒；随后读取目标播放器状态 | 目标返回 `LOGIN_REQUIRED` 和机器人验证提示，零条媒体格式，播放器无媒体地址 |
| bgutil 2.0.2，强制 `fetch_pot=always` 并跳过网页初始 player response | 日志确认实际生成 **Player PO Token**，分别请求 web / mweb | 目标仍要求机器人验证；不能把 Provider 加载成功等同于视频可用 |
| WPC Provider 1.1.2 + nodriver 0.50.3 + Chromium + Xvfb | 日志确认在浏览器中实际生成 Player / GVS Token；对照能解析出 5 种格式 | 目标仍要求机器人验证；没有将这个实验性 Provider 加入生产依赖 |
| yt-dlp master `43a45ae`（2026-10-21） | 使用独立源码挂载与 Deno，实际请求目标 | 最新主分支默认客户端仍失败；android_vr、web_embedded、web_safari 分别测试也失败 |
| 同一主分支的 iOS / tv_simply 客户端 | 各请求一次目标 | 都返回机器人验证，没有采用全客户端轮询 |
| curl_cffi，显式 Chrome TLS impersonation | 临时安装并通过 yt-dlp 的 `impersonate` 参数请求目标 | 同样返回机器人验证 |
| 服务器已有固定 IPv6 地址 | 绑定现有 IPv6 源地址请求目标和第二条公开视频 `YE7VzlLtp-4` | 两条都被要求机器人验证；没有改 Docker 网络或进行地址轮换 |
| Invidious Companion 官方镜像 | 自建临时实例，使用它的 HTTP player API 请求目标与对照 | 后台始终未验证到有效 Token，两条请求都返回“正在启动，等待有效 potoken”；未达到可测视频的就绪状态，因此不能据此断言初始化成功后目标一定失败 |

Invidious Companion 本次使用的镜像摘要为 `sha256:5642790b67c2ce655a630dbff1342051bd98674abc8e469551117a3d4b6bedcc`。没有使用公共实例转发用户链接。更旧的 `youtube-trusted-session-generator` 已被其维护者标记失效，不作为候选。

第二轮结果说明，当时的默认出口已经在原生网页播放器获取媒体之前失败。该轮独立替换组件的做法没有解除限制；第三轮随后验证了同一原生会话、正确 STS、SABR 和 WARP 的组合。

仅在本地解析出 URL、随后交给服务器换出口下载，不能算这条链路成功。因此第三轮把浏览器、播放器 API 和媒体全部放在同一出口，并实际下载和解码验收。

### 第三轮：原生会话与 SABR 实验

继续研究 NewPipe、YouTube.js、WPC Provider 和 yt-dlp 维护者的 SABR 分支后，实现了 [`docker/youtube/probe.py`](../docker/youtube/probe.py)，并提供独立的 `experiments/youtube-sabr/Dockerfile`。下载器不导入应用、统计或生产数据目录，不替换主应用的 yt-dlp。

目前验证到的进展：

- 独立启动有界面 Chromium，不传入 `--enable-automation`，再通过 CDP 连接，同一会话生成目标内容绑定的 Player Token。目标 `iN1N8o3Miv4` 的 player API 能返回 `OK`、30 条分轨信息和 592 秒时长。仅搬运匿名 Cookie 给普通 yt-dlp 不足以完成下载。
- 原生网页中的 15.041 秒播放实际为片头广告。广告结束时目标内容时长为 592 秒，但播放时间仍为 0。不能把广告播放、player 状态 `OK` 或 SABR HTTP 200 直接记为视频恢复。
- 自建适配器最初漏传 `signatureTimestamp`，导致元数据能返回、媒体 URL 却不匹配实际播放器版本。现在先从实际加载的 `base.js` 提取 STS，传入初次请求和刷新请求，并保留完整原始 HTML 中的初始 attestation。
- 修正后的组合链路（浏览器 player API + bgutil 媒体 Token + SABR）已完整下载对照 `dQw4w9WgXcQ`：29,969,206 字节，H.264 / AAC，1280×720，213.089524 秒，完整 ffmpeg 解码通过。原版 SABR 分支配 bgutil 也能完整下载这个对照，说明 SABR 编解码本身已具备正向验证。
- 默认出口下，目标仍会在 SABR 刷新阶段返回 `UNPLAYABLE / Video unavailable`。将整个浏览器会话、播放器 API 和 SABR 媒体请求都放到现有 WARP 出口后，目标已连续两次在全新匿名会话中完整下载成功：18,201,366 字节，H.264 / AAC，1280×720，592.108844 秒，完整解码通过。抽取第 30 秒画面也确认是目标韩语教学内容。
- 同方法恢复了第二条此前触发机器人验证的视频 `YE7VzlLtp-4`：104,040,033 字节，H.264 / AAC，1280×720，596.544 秒，完整解码通过。这两条成功均使用浏览器原生媒体 Token，不依赖账号 Cookie 或 bgutil。

实验冻结 SABR 源码提交 `54f9a7b75e2b4a7611fdf47aa3227b809382386e`、Playwright 1.64.0、Deno 2.9.8、EJS 0.8.0、protobug 1.0.0、bgutil 插件 2.0.2。可构建独立环境：

```bash
docker build -f experiments/youtube-sabr/Dockerfile -t youtube-sabr-experiment .
docker run --rm --memory=2g \
  --mount type=volume,src=youtube-sabr-experiment-output,dst=/output \
  --network shizhen_default \
  youtube-sabr-experiment iN1N8o3Miv4 --proxy http://warp:1080 --activate
```

需要对比媒体 Token 实现时，加 `--pot-url http://<自己的-bgutil-2.0.2>:4416`，并让实验容器能连接自己的服务。`--activate` 使用播放器快捷键激活页面；`--transport browser` 对比由浏览器发起媒体请求。Cookie 和 Token 只留在临时会话中，不输出其值或媒体签名 URL。

上面的网络名和 `warp` 服务名来自本项目 Compose 部署；其它部署要改为自己的网络和代理地址。成功组合中的必要条件是同一出口、实际播放器对应的 STS、同一浏览器会话中的内容绑定 Token，以及可处理 SABR 的下载器。单独换 WARP 或单独补 Token 的旧测试不能代替这条完整链路。

只有正确目标 ID、完整合并文件、音视频两轨、匹配的完整时长和全文件解码全部通过，程序才打印 `stage=verified` 并退出 0。解析失败、下载失败或把其它时长的视频当作目标都会退出非零；超时由容器入口限制为 300 秒。输出文件保存在独立命名卷中。

### 应用接入

主镜像使用 `/opt/youtube-native/venv` 安装冻结版本的下载器，普通平台继续使用正式 yt-dlp。原来的自动升级仅更新正式版本，避免把实验分支混入其它平台。原生路径不依赖 bgutil；独立实验中的 bgutil 只用于对照。

解析优先使用已有验证缓存或现有下载器，并对 YouTube 返回的直链读取 1 KB。默认路径解析失败、没有合流直链或媒体被拒绝时，备用出口和原生会话并行恢复，只接受先完成且通过媒体验证的结果；已知删除、私密、限龄和需要登录的内容不进入该兜底。原生路径在解析时下载整条视频（默认 720p，估算超过解析预算就自动降清晰度，见下文「时间预算」），校验后注册现有 Source 缓存。应用只返回已签名的缓存标识，通过原来的 `/api/proxy` 提供 Range 预览和下载；GIF / 实况转换直接复用本地文件。更高清晰度按需重新下载，仅音频从已验证缓存提取。

单进程默认最多同时启动 2 个原生会话（每个约 0.5 GB 内存），受现有源文件大小、磁盘配额和超时约束。临时会话退出后删除，超时和取消会回收本次登记的 Chromium / Xvfb 进程组；Compose 的 `init: true` 负责子进程回收。缓存过期返回 410，提示重新解析，不把缓存标识当成外部地址访问。

配置项：

| 变量 | 默认 / 用法 |
|---|---|
| `PARSE_VIDEO_YOUTUBE_NATIVE` | `1`；设 `0` 关闭 |
| `PARSE_VIDEO_YOUTUBE_NATIVE_PYTHON` | Docker 为 `/opt/youtube-native/venv/bin/python`；手动部署须指向独立安装原生下载器依赖的解释器 |
| `PARSE_VIDEO_YOUTUBE_NATIVE_SCRIPT` | Docker 为 `/opt/youtube-native/probe.py`；源码运行默认为 `docker/youtube/probe.py` |
| `PARSE_VIDEO_YOUTUBE_NATIVE_PROXY` | 可选无认证 HTTP 代理；默认依次使用专用 YouTube 代理、备用 WARP、通用代理；Chromium 与下载器均使用同一个地址 |
| `PARSE_VIDEO_YOUTUBE_NATIVE_TIMEOUT` | `75` 秒，解析时的兜底上限，包含开浏览器、下载和校验；接口总超时仍为 90 秒 |
| `PARSE_VIDEO_YOUTUBE_NATIVE_PARSE_BYTES` | `125829120`（120 MiB）；解析时估算超过就降一档清晰度，最低一档也放不下时报「视频太大」 |
| `PARSE_VIDEO_YOUTUBE_NATIVE_CONCURRENCY` | `2`；同时运行的浏览器会话数。解析最多等 10 秒名额，下载任务一直等 |

自检的普通解析预算为 60 秒；启用原生兜底的 YouTube 自检额外保留一次原生下载预算。自检不读原生缓存（缓存被读到就续期，自检每小时一次，读缓存的话第一次成功后会一直报正常），每次现拿一份 144p。其它平台的自检上限不变。

应用镜像的实际接口验收（Python 3.12，独立数据目录，复用现有 WARP，不连接 bgutil、不提供账号 Cookie）：

| 操作 | 实测结果 |
|---|---|
| 正常调用 `/api/parse`，目标 `iN1N8o3Miv4` | 自动进入原生兜底，无模拟失败；63.7 秒返回已验证的 720p 缓存，592.108844 秒 |
| `/api/proxy` 预览 | 连续 5 次 Range 请求均为 206，各返回正确的 1,024 字节；无效签名为 403 |
| `/api/prepare` | 立即命中同一缓存，探测帧率 30 fps |
| `/api/convert` | 第 30–32 秒、320px、8 fps 的 GIF 成功，24,041 字节 |
| `/api/download` 仅音频 | 成功提取 m4a，9,576,729 字节 |
| `/api/download` 1080p | 完整下载并解码验证，22,964,903 字节；应用探测为 1080p、有音频、592.11 秒 |
| 自检媒体检查 | 读取已验证的本地缓存成功，不重新请求签名媒体地址 |

隔离接口测试容器应使用 `--no-healthcheck`：TestClient 不监听 8000 端口，继承网站镜像的健康检查会被本机自动保活机制反复重启，不能把这种中断算作下载器失败。最终上述验收是在关闭测试容器健康检查后完整退出 0 的结果。

接入阶段回归：Python 3.11 与 Python 3.12 各 380 项测试通过，Ruff 检查与格式检查通过，主镜像构建成功。测试覆盖媒体 403 自动兜底、错误分类保留、缓存签名和 Range、过期文件、错误视频 / 路径 / 缺失音轨拒绝，以及超时和取消后的子进程回收。线上服务未替换。

### 性能优化

进一步分段测量，基线 helper 的 Chromium 启动约 1 秒，预热与播放器初始化累计到 26.3 秒，下载约 6 秒，完整解码约 11.4 秒。原应用从普通解析失败一路执行到原生缓存返回，实测为 63.7 秒。优化保留相同的 720p、目标 ID、完整时长、双轨与全文件解码要求。

实现调整：

- 默认路径失败后，备用出口与原生兜底并行恢复。直链仍需读到媒体字节，原生仍需完整下载验证；先成功者返回并取消其它异步任务。已经进入线程池的普通下载器调用仍按原网络超时自行结束。默认路径正常时不启动浏览器。
- 保留对照视频预热，把快速路径固定等待从约 19 秒缩到 5 秒，再检查访客上下文、WebPoClient 和实际播放器脚本是否就绪。初始化或播放器请求未成功时回退原来的等待流程；`--compatibility` 可直接运行原流程。
- 下载完成即关闭浏览器及播放器，再做完整解码，减少验证阶段的内存与 CPU 竞争。解码并发限制为 4 个线程；本机同文件单线程 / 双线程 / 四线程校验分别约 23.9 / 14.4 / 9.3 秒，四线程仍检查全文件。
- 同一分享链接的并发请求共用一次抓取，只扣一次全站上游请求配额。一个等待者取消不影响其它等待者；全部取消时停止任务。
- 原生缓存按视频 ID 和清晰度复用，长链接、短链接、时间戳分享都可直接命中。命中在浏览器信号量之前返回，不必排队等待其它视频。文件被配额清理后，解析缓存同步失效，重新解析。

首轮优化应用接口实测：`iN1N8o3Miv4` 的首次正常请求 42.8 秒返回已验证的 720p 视频；同一链接再次解析 3.32 毫秒，短链接别名 18.33 毫秒。720p Range 预览、GIF、音频、1080p 完整下载与自检均通过。进一步缩短等待后，独立 helper 的同视频验收为 39.07 秒；第二条 `YE7VzlLtp-4` 为 48.92 秒、104,040,033 字节，仍为 720p、有音频、596.544 秒并通过完整解码。

最终镜像 `shizhen-youtube-native-app:20261021-perf` 再次以全新数据目录验收：首次正常解析 **36.9 秒**（原 63.7 秒，本次耗时减少约 42%），包括正确目标的完整下载与解码校验；同一链接缓存 **3.29 毫秒**，短链接别名 **18.08 毫秒**。连续 5 次正确 Range 响应、签名拒绝、原视频准备、GIF、音频、1080p 下载和状态媒体检查全部通过；1080p helper 耗时 48.02 秒，文件 22,964,903 字节、592.11 秒且含音频。没有替换或重启线上容器。

上述为同机器单次观测，包含网络、YouTube 风控与下载速度波动，不能作为所有视频的固定耗时。新增并发恢复、取消、同链接合并、缓存别名及清理后的失效检查，Python 3.11 / 3.12 各 389 项测试通过。

手动部署需要 `docker/youtube/requirements.txt` 中的独立依赖、FFmpeg、Xvfb 和 Playwright Chromium。只安装普通 `youtube` extra 不会自动启用原生路径。该 helper 目前只支持无认证 HTTP 代理，适用于项目现有的 `http://warp:1080`；带账号密码的专用出口可为 helper 配置独立的无认证内网代理。

该路径首次解析会实际下载整条视频，延迟与文件大小相关；长视频或慢网络可能触发配额 / 超时。播放器内部接口及 SABR 分支仍可能随 YouTube 改版而失效，需要保留固定样本持续验证。高于 1080p 会使用可用的 VP9 / AV1 视频轨，不强行选择最高只有 1080p 的 H.264。

### 时间预算（2026-10-22）

上面的方案在解析时下载并完整解码整条 720p。用 10 分 35 秒的 720p60 视频 `aqz-KE-bpKQ` 经 WARP 实测：开浏览器到拿到 player 响应 15 秒，下载 182 MB 用 27 秒（约 6.7 MB/s），完整解码 39 秒，合计 82 秒，超过 75 秒的兜底上限，解析失败。

调整：

- 校验改为整条只解封装（检查缺段、截断），画面只在开头、中间、结尾各解码 2 秒。同一视频同一清晰度从 82 秒降到 38.9 秒。
- 下载前按 player 响应里各分轨的 `contentLength` 估算大小。解析时超过 `PARSE_VIDEO_YOUTUBE_NATIVE_PARSE_BYTES` 就降一档：上面的视频解析 31.2 秒拿到 480p，720p 及以上作为下载选项。最低一档也放不下就报 `too_large`，不白等到超时。
- 下载 / 转换任务（用户选的 720p、1080p、仅音频）不受解析的 90 秒限制，上限为任务时限减 30 秒（默认 270 秒），按选的清晰度严格下载，超过 300 MB 上限直接报错。同一视频 720p 任务 42.8 秒、181,649,549 字节，进度条按估算大小推进。解析时下好的那份清晰度一样就直接用，原视频交给任务时用硬链接，不再复制。
- 状态页自检绕开缓存，取 144p，实测 27.4 秒。

### 升级原生下载器

`docker/youtube/requirements.txt` 里的 yt-dlp 是 SABR 实验分支的固定提交，带 sha256 校验，不在 `PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS` 的自动升级范围里。状态页 YouTube 报失败、日志里原生会话报错时，先去 [bashonly/yt-dlp](https://github.com/bashonly/yt-dlp) 看 SABR 分支有没有新提交，或者上游是否已合并 SABR；换提交时重新计算哈希：

```bash
curl -sL https://github.com/bashonly/yt-dlp/archive/<提交>.tar.gz | sha256sum
```

同时更新 `experiments/youtube-sabr/Dockerfile` 里的同一行。

## 配置与验证

Docker 镜像安装完整的 `youtube` 依赖组。手动安装：

```bash
python -m pip install -e ".[web,cli,youtube]"
python -m parse_video_py.diag "https://www.youtube.com/watch?v=<视频 ID>"
```

`/api/health` 返回的 `js_runtime` 应为 `deno` 或可用的 `node`，`youtube_native` 表示原生兜底是否已配置并启用。诊断命令也显示原生路径配置、yt-dlp、EJS、PO Token 插件版本以及是否配置专用代理，不打印凭证。版本存在不等于服务可达，仍需真实链接和媒体验证。

如果当前出口被限制，可以配置一个已经确认可访问目标视频的 HTTP 代理：

```bash
PARSE_VIDEO_PROXY_YOUTUBE=http://user:password@proxy-host:port
```

该配置只影响 YouTube 及其媒体域名。页面解析、合并下载和 CDN 转发使用同一出口；bgutil 插件也会将选定代理传给 Token 服务。保持出口一致，避免解析出来的地址换出口后失效。已有 WARP 仍作为一次性的备用路径。

需要账号才能访问的内容必须提供具有相应访问权限的有效 Cookie。现有 `PARSE_VIDEO_COOKIES_FILE` / `PARSE_VIDEO_COOKIES_BROWSER` 可用，但不应把账号 Cookie 视作永久凭证。此处没有现成账号或新的专用出口，因此未验证这两种条件下受限样本能否恢复。

## 上游资料

- [yt-dlp EJS 指南](https://github.com/yt-dlp/yt-dlp/wiki/EJS)：脚本和 JS 运行时是两项依赖；Deno 默认启用，Node 需要显式启用。
- [yt-dlp PO Token 指南](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)：解释不同客户端和请求上下文的 Token 要求；建议使用自动 Provider。
- [bgutil README](https://github.com/Brainicism/bgutil-ytdlp-pot-provider#verification)：列出 Provider 只说明加载成功，要看实际生成 Token 的日志；Token 不保证解除 IP 风控。
- [bgutil issue #37](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/issues/37)：维护者说明机房出口仍可能被要求登录，不能单靠 PO Token 解决。
- [yt-dlp 更新渠道](https://github.com/yt-dlp/yt-dlp#update)：nightly 可用于验证新修复；本次已测试，未解决目标样本的机器人验证。
- [WPC Provider](https://github.com/coletdjnz/yt-dlp-getpot-wpc)：通过真实浏览器生成 Token 的实验性实现，本次已实际验证生成过程。
- [Invidious Companion HTTP API](https://github.com/iv-org/invidious-companion/wiki/How-to-communicate-with-Invidious-companion-with-any-client-(HTTP-API))：本次在自建临时实例上验证 player API，未完成 Token 初始化。
- [旧可信会话生成器的弃用说明](https://github.com/iv-org/youtube-trusted-session-generator)：维护者说明旧实现不再有效，并指向 Companion。
- [yt-dlp issue #15865](https://github.com/yt-dlp/yt-dlp/issues/15865)：上游也区分所有客户端均被要求机器人验证的情况与缺少 PO Token 的问题。
- [维护者的 SABR 分支](https://github.com/bashonly/yt-dlp/tree/sabr)：第三轮使用冻结提交进行完整音视频下载实验。
- [NewPipe 的媒体请求实现](https://github.com/TeamNewPipe/NewPipeExtractor/blob/dev/extractor/src/main/java/org/schabi/newpipe/extractor/services/youtube/YoutubeStreamHelper.java)：先获得 visitorData，再请求播放器；本机复现 visionOS 路径后，目标仍被要求机器人验证。
- [googlevideo](https://github.com/LuanRT/googlevideo)：UMP / SABR 协议、媒体流与内容绑定 Token 的实现资料。
