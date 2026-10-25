# AGENTS.md

本文件适用于整个仓库。求原图（原名「拾帧」，仓库仍名 `shizhen`）提供分享链接解析、媒体下载和 GIF / 实况照片 / 动态照片转换。
在用户明确要求调整架构、设计或流程时，以本次任务为准，并同步相关约定。

## 开发入口与事实来源

- Python 支持 3.10+；CI 和应用镜像使用 3.12。前端是 Jinja2 模板、CSS 和浏览器原生 ES 模块。
- 在仓库根目录创建或复用虚拟环境，安装 `python -m pip install -e ".[web,cli,dev]"`，用 `python main.py` 启动。以下命令均在根目录、已激活的虚拟环境中运行。
- [README.md](README.md#贡献) 说明开发约定；[pyproject.toml](pyproject.toml)、[eslint.config.mjs](eslint.config.mjs) 和 [CI 工作流](.github/workflows/ci.yml) 是工具规则和检查命令的事实来源。修改这些配置时同步本文件。
- 先读目标模块和相邻测试，沿用已有封装；修改范围围绕当前任务，避免顺带重构无关代码。

## 代码分层

下列路径均相对于 `src/parse_video_py/`：

| 位置 | 职责与修改边界 |
| --- | --- |
| `parser/` | 链接 → `VideoInfo`；不处理 Web 路由。新平台继承 `BaseParser`，在 `parser/__init__.py` 注册域名。 |
| `convert/` | 下载、媒体转换、任务队列和产物存储；不依赖 FastAPI。 |
| `web/` | 路由、参数校验、鉴权、限流和响应；解析与转换逻辑交给前两层。 |
| `templates/`、`static/site.css` | 页面结构和视觉样式；公共布局复用 `base.html`。 |
| `static/js/` | 按功能组织交互；复用 `api.js` 的请求封装和 `dom.js` 的 DOM 工具。 |
| `seo.py`、`guides.py` | 落地页、教程和结构化内容；避免在路由或模板中复制同一份文案数据。 |

- 解析器用 `BaseParser.get_json()` / `get_text()` 发请求，以 `ParseError` 表达失败原因。同步上游修复时按差异迁移，不能用上游文件整份覆盖本地解析器。
- 保留 `VideoInfo` 的语义：可直接下载的清晰度放 `formats` 并带 `url`，需服务端合并的格式放 `format_spec`。
- 修改接口时检查前端调用方和现有兼容接口；新增进度能力时保留 JSON 解析接口的可用性。配置仍通过 `PARSE_VIDEO_*` 环境变量提供。

## 产品与界面设计

- 延续现有编辑风格：冷暖浅底、深色正文、衬线标题、无衬线操作文字、等宽技术信息，蓝色强调搭配少量薄荷绿和橙色。优先复用 [site.css](src/parse_video_py/static/site.css) 的变量、`.wrap`、`.btn`、`.card` 和现有间距层级。
- 页面围绕「粘贴 / 上传 → 解析 → 下载或转换」组织。让当前主要操作清楚可见，次要选项按需展开；新增功能延续现有组件与交互方式。
- 中文文案简洁、自然，解释用户能做什么、当前状态和下一步。状态提示可沿用现有轻松语气，但阶段与进度必须来自实际事件；不要用等待时长伪造百分比，也不要展示内部服务、凭证或服务器路径。
- 修改布局或交互后，在桌面和手机窄屏检查相关状态：空态、等待、成功、失败，以及清空 / 换链接。确认无意外横向溢出、遮挡或重复提交；旧请求和计时器不能覆盖新结果。
- 保留语义化控件、可访问名称、键盘操作、可见焦点和状态播报。动画遵循 `prefers-reduced-motion`，触屏操作不能只依赖 hover。
- 字体与界面资源继续自托管。前端逻辑写在 ES 模块中，模板通过 `js_url()` 加载；普通静态资源用 `static_url()`，模块内部用相对 import，保持现有内容哈希和预加载机制。
- 保留现有 CSP：不新增可执行内联脚本或 HTML 事件属性，不通过放宽 `script-src` 让新功能运行。现有关键 CSS 内联和 JSON-LD 按原机制维护。
- 改标题、落地页或教程文案时检查字体覆盖。需要新字时运行 `python scripts/subset_fonts.py --src <NotoSerifCJK-OTF目录>`，同步提交生成的字体与 `fonts.css`；验证 `tests/test_fonts.py`，保留字体分片和体积限制。缺少字体源时明确报告，不用系统字体回退掩盖缺字。
- 修改 `.sec` 背景或层级前阅读 CSS 中的层叠说明；其暖色背景与色团使用负 `z-index`，给祖先添加 `transform`、`filter` 或新的层叠上下文可能改变显示。

## 代码风格与复杂度

Python 由 Ruff 格式化、检查 import 顺序、常见错误和复杂度；使用兼容 Python 3.10 的类型语法。JavaScript 沿用附近代码的两空格缩进、单引号、分号及 `const` / `let`，保持 ES2022 兼容。

| 范围 | 当前约束 | 配置位置 |
| --- | --- | --- |
| Python | 行宽 120；圈复杂度 ≤ 10；每函数返回语句 ≤ 8 | `pyproject.toml` 中的 Ruff 配置 |
| Python | 同时启用分支数、语句数检查 `PLR0912` / `PLR0915`；阈值使用 Ruff 默认值 | `tool.ruff.lint.select` |
| JavaScript | 圈复杂度 ≤ 10；嵌套深度 ≤ 4；回调嵌套 ≤ 3 | `eslint.config.mjs` |
| JavaScript | 参数 ≤ 5；每函数 ≤ 80 行（不计空行、注释） | `eslint.config.mjs` |

- 超限时按职责提取函数、分离数据与控制流、减少重复分支；函数名要说明行为。不要为凑阈值机械切碎代码，也不要用提高阈值、`noqa`、`eslint-disable` 或排除文件掩盖问题。
- 保留已有有理由的例外：`seo.py` 与 `guides.py` 仅豁免 `E501`，不是免除其他检查。新增例外需有具体原因和最小作用范围；任务未要求时不放宽现有质量门槛。
- 异步路径复用现有客户端、任务和子进程封装，避免在事件循环中加入阻塞网络或媒体处理。保留超时、取消、子进程回收与并发配额。
- 注释说明非显然的原因、兼容约束和失败模式；面向没有本次对话上下文的维护者，不记录提示词或迭代过程。

## 提交信息

采用 Conventional Commits：`type(scope): 中文动作与具体结果`。类型和范围用小写英文，冒号后留一个空格；范围可省略，优先使用 `parser`、`convert`、`web`、`ui`、`fonts`、`agents` 等稳定模块名。

| 类型 | 用途 |
| --- | --- |
| `feat` | 新增功能 |
| `fix` | 修复错误 |
| `perf` | 性能优化 |
| `refactor` | 调整代码结构，保持行为不变 |
| `docs` | 文档和开发约定 |
| `test` | 测试用例 |
| `ci` | CI/CD 工作流 |
| `build` | 依赖、构建和镜像 |
| `style` | 代码格式调整 |
| `chore` | 不属于上述类型的维护事项，少用 |

- 标题简短、具体，说明改变了什么，避免只有「优化代码」「修复问题」「更新文档」。触发条件、修改理由和必要的验证结果放正文，与标题空一行。
- 一次提交围绕一个目的；修复与对应回归测试放在一起，无关重构分开。按主要目的选类型，修复时拆函数仍用 `fix`；界面新增功能或显示缺陷分别用 `feat(ui)`、`fix(ui)`，不归为代码格式 `style`。
- 不兼容变更在冒号前标记 `!`，并在正文或 `BREAKING CHANGE:` 脚注中说明影响和迁移办法。
- 示例：`fix(parser): 小红书重试失败时保留原始错误原因`、`feat(ui): 展示解析阶段与已等待时间`、`docs(agents): 添加设计、代码风格与 CI 约定`。

## 容易破坏的项目约束

- 出站 HTTP 使用 `utils.create_async_client()` 或 `convert.net.safe_client()`，保留 SSRF 检查、重定向逐跳校验和 Cookie 隔离；业务代码不要另建裸 `httpx.AsyncClient()`。
- 保留 `uvicorn` + `httptools` 的依赖选择；`pyproject.toml` 明确禁止引入绕过现有 SSRF 防线的 `uvloop`，不要换成 `uvicorn[standard]`。
- 下载、代理和转换入口继续校验签名、限流与配额。外部命令使用参数列表，沿用现有执行封装。
- `tests/conftest.py` 在模块导入前设置临时数据目录和测试配置。新增测试沿用此隔离，不读取或写入真实 `data/`、密钥和账号 Cookie；平台响应使用离线替身。

## 验证与 CI

开发中先运行受影响的测试；代码交付前按下表补齐对应检查。纯文档改动检查事实、链接和 `git diff --check` 即可；界面检查不能由 lint 或模板测试替代。

| 改动 | 本地验证 |
| --- | --- |
| Python 源码、测试或依赖 | `python -m ruff format --check .`、`python -m ruff check .`、`python -m pytest -q` |
| 前端 JavaScript | `npx --yes eslint@10 src/parse_video_py/static/js`，以及相关页面的浏览器交互检查 |
| 模板、样式、静态资源或页面文案 | `python -m pytest -q tests/test_assets.py tests/test_fonts.py tests/test_csp.py tests/test_seo_routes.py`，以及受影响页面的桌面 / 手机检查 |
| 部署脚本 | `shellcheck scripts/deploy.sh` |
| `Dockerfile`、`pyproject.toml`、`requirements.txt` | 具备 Docker 环境时执行 `docker build -t shizhen-ci .`；CI 对这些文件的变化执行镜像构建 |

- CI 的测试 job 使用 Python 3.12、Node.js 22，执行 Ruff、ESLint、全量 pytest 和 ShellCheck；命令以 [.github/workflows/ci.yml](.github/workflows/ci.yml) 为准。
- 本地 `.pre-commit-config.yaml` 仅运行 Ruff 检查及格式化，且可能自动修改文件；它不覆盖 ESLint、测试或完整 CI。
- 解析、下载、转换行为的交付还应按 [README 的贡献说明](README.md#贡献) 做真实链接与三种格式的烟测。外网验证与离线测试分开；受平台风控、凭证或工具限制时，说明未验证的部分，不能把替身测试当作真实平台成功。
- `push` 到 `main` 且 CI 通过会自动部署生产环境，流程见 [docs/deploy.md](docs/deploy.md)。不要把推送 `main` 或运行 `scripts/deploy.sh` 当成本地验证步骤；仅在任务已授权发布时执行。
- 交付说明写清行为变化、已执行检查及结果、尚未验证的部分。规则与实现不符时核对上述事实来源并修订本文件，不继续叠加矛盾条款。
