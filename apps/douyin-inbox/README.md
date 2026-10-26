# 抖音普通账号助手

用户把作品、评论图片或表情包转发给一个普通抖音账号，助手解析后在同一私信中回传。
视频和完整实况先询问格式，用户选择原格式、GIF 或两者；实况原格式保存到 iPhone 后为 LIVE。

这是 [shizhen / 求原图](../../README.md) 仓库中的独立应用，包名为 `qiuyuantu-inbox`，
Python 模块为 `qiuyuantu_inbox`。源码、测试、部署模板和操作文档都在本目录管理。
它复用主项目的解析器、安全下载和媒体转换，单独安装、单独运行；主项目 Web 镜像不包含此应用。

## 能力与边界

| 输入 | 回传与交互 |
| --- | --- |
| 作品图片 / 图集 | 原图逐项回传 |
| 普通短视频作品 | 回复 `1` 取视频、`2` 取 GIF、`3` 两者都要 |
| 完整实况作品 | 回复 `1` 取 LIVE、`2` 取 GIF、`3` 两者都要；照片和视频逐项配对 |
| 评论静态图 / GIF | 提取评论自身素材；GIF 保留动画 |
| 私信相册实况 | 原格式分享保留 LIVE，选择 GIF 后才提取配套视频 |

多条待选择时按提示回复 `3 编号`，编号可用前 4 位，重号时补全；若先回复了 `GIF` 等格式，
再单独补编号即可，提示会列出各编号对应的作品标题。也可发送 `进度 完整编号`、`取消 完整编号`。
GIF 默认转换每段视频的前 6 秒，最长边 480 像素、12 帧/秒，并限制文件体积。

图片、GIF、普通视频及实况已做真实账号验证，接收方确认 LIVE 可保存、播放。
部分评论卡片只有静态预览，取不到原始视频时会说明原因，不能静默返回静态图冒充 LIVE。
陌生人消息请求入口、普通长视频和推广后的持续高峰容量仍需验证。

## 安装与启动

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

扫码、验证码由账号持有人在浏览器完成；无桌面服务器的远程窗口配置见[操作手册](docs/operations.md)。
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

## 运行与配置

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
全部参数、实现设计及真实验证记录见[操作手册](docs/operations.md)。

## 开发与部署

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
部署模板见 [deploy/](deploy/)，安装前核对解释器与账号目录；代码提交不会自动替你启用 `--send`。
修改本 README 后运行 `python apps/douyin-inbox/scripts/sync_readme.py`，同步主 README 的可折叠内嵌内容。
