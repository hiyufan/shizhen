# 搜索表现与推广来源

`/stats` 的访问量和来源统计使用同一个 `PARSE_VIDEO_STATS_TOKEN` 开关与鉴权。来源数据随正常页面请求记录，不需要第三方脚本；部署新版本后开始采集，旧 `events` 保持不变，不能从历史 IP 或页面路径推测来源。

## 先看哪些数字

| 数据 | 在哪里看 | 口径 |
| --- | --- | --- |
| 搜索展现、点击、CTR、平均排名 | `/stats` → 搜索表现，或三家站长平台的效果报表 | 搜索引擎报告；站内日志不能推算未点击的展现或完整搜索词。当前 Windsor 的 Bing 数据源不提供排名 |
| 搜索带来的会话和落地页 | `/stats` → 搜索带来的访问 | 根据入站 Referer 或明确标记的 organic 来源识别 |
| 推广来源和活动 | `/stats` → 搜索与推广来源 | UTM 优先，其次是外部引荐域名 |
| 成功解析、下载行为、转换成功 | 同一来源或落地页的行 | 按访问会话去重，分母是所选期间内开始的会话 |

三家站长平台的时间范围、时区和搜索类型要分别记录。Google Search Console 的 API 日期按太平洋时间解释；站内统计使用浏览器时区。对比时选完整日期，并保留平台显示的数据截止日期，不将尚未出齐的当天数据与完整一天比较。

搜索点击和本站会话是两个口径：浏览器缓存、来源被应用隐藏、Cookie 被禁用、重复点击、机器人过滤和数据延迟都会产生差异。没有报表时写“未接入”，不能用 0 占位，也不能把三家平均排名直接取平均。查看报表需要对应站点的读取权限，站点验证文件本身不等于报表访问授权。

官方入口：[Google Search Console](https://search.google.com/search-console)、[百度搜索资源平台](https://ziyuan.baidu.com/)、[Bing Webmaster Tools](https://www.bing.com/webmasters/)。

## 绑定 Google / Bing 报表

网站通过 Windsor Connectors API 读取已授权的站点；在 ChatGPT 中连接 Windsor 只授予对话工具访问权，不会自动给网站配置 API Key。先在 Windsor 连接 Google Search Console / Bing Webmaster 并选择站点，然后在服务器根目录的 `.env` 中配置：

```dotenv
PARSE_VIDEO_WINDSOR_API_KEY=<你的 Windsor API Key>
PARSE_VIDEO_SEARCH_GOOGLE_SITE=sc-domain:example.com
PARSE_VIDEO_SEARCH_BING_SITE=https://example.com
```

站点标识须与 Windsor 已连接账户完全一致；Google 也支持已授权的网址前缀资源。密钥仅保存在服务器，`.env` 使用 `chmod 600 .env`，已被 Git 和 Docker 构建上下文排除。不要把密钥贴到网页、聊天、提交或日志中。`docker-compose.yml` 会将这三个变量传入应用；还需要原有的 `PARSE_VIDEO_STATS_TOKEN`。直接运行 Python 时须先向进程注入环境变量，`main.py` 不会自动加载 `.env`。新增配置在应用重新启动后生效，按已有发布流程部署。

应用启动后在后台同步，以后每 6 小时更新；失败后 15 分钟重试。同一进程不会重复同步同一平台。页面通过受统计口令保护的 `/api/stats/search` 读取本地快照，刷新页面不会直接调用 Windsor。页面提供 7 / 28 / 90 天范围，以各平台最新返回的日期为截止日，与前一等长期间比较；两家平台可能具有不同的日期范围。同步失败保留上一次成功的完整快照，并显示失败或旧报表状态。未绑定、等待首次同步、空报表和同步失败都不显示为零。

快照保存在 `data/search_reports.db`，每个平台只保留最近一次成功获取的 180 天报表；成功换绑站点后删除该平台旧站点快照。同步参数显式排除当天；Google 关闭未定稿数据、仅取网页搜索，按太平洋时间解释日期。Bing 沿用平台返回日期，其当前连接器按关键词记录汇总，可能不包含后台总量中的全部查询，不提供页面和排名字段。未返回的日期不补零；最新记录日期不保证等同于平台的结算完成日期。

总量单独请求日期维度，不从页面或关键词明细反推；CTR 使用点击 / 展现，Google 平均排名按展现加权，不对两家排名求平均。关键词 / 页面按点击数、展现排序，界面显示前 20 项。Google 的匿名查询、上游行数限制可能使明细之和小于总量；达到单次 50,000 行上限时明确标记明细不完整，总量达到上限则不更新快照。

聚合关键词仅来自站长平台，不从浏览器来源读取、不与 IP 或访问会话关联。报表中的页面地址去除查询参数和 fragment。API Key 不入数据库、不回传浏览器，HTTP 客户端日志在报表请求期间屏蔽，错误只保存固定错误码。百度暂未自动同步，继续使用其后台报表。

接口和凭证说明：[Windsor API 文档](https://windsor.ai/api-documentation/)。

## 推广链接

在 `/stats` 展开“生成推广链接”，选择站内落地页并填写来源、媒介和推广标记。地址使用配置的 `PARSE_VIDEO_SITE_URL`，不包含统计口令。分享给用户的是生成的公开工具页链接。

中英文 README 指向本站公开页面的链接统一使用 `github / referral / readme`，具体入口可在落地页表中区分。下表其余场景是命名示例，不代表已经投稿或投放；只有实际发布时才使用对应标记，自然收录的外链保留域名归因。

| 场景 | `utm_source` | `utm_medium` | `utm_campaign` |
| --- | --- | --- | --- |
| GitHub README | `github` | `referral` | `readme` |
| HelloGitHub 自荐 | `github` | `referral` | `hellogithub` |
| 中文独立开发者目录 | `github` | `referral` | `indie-directory` |
| Telegram 资源频道 | `telegram` | `social` | `noisedh` |
| 某次 Steam 工具介绍 | `telegram` | `social` | `steam-assets-202610` |

例如：`https://ynvan.com/steam-assets?utm_source=telegram&utm_medium=social&utm_campaign=steam-assets-202610`。

标记仅接受 1–64 个小写英文字母、数字、点、横线和下划线，首字符须是字母或数字；无效字段丢弃。不要把账号、邮箱、分享链接或密钥填进标记。来源页面只保存规范化域名，落地页只保存已知站内路径，不保存 Referer 的路径、搜索词、查询参数或 fragment，也不保存 `utm_term` / `utm_content`。

没有标记的旧链接仍可能识别出 GitHub、搜索引擎等域名，但不能区分同一域名的多次投稿；隐藏来源的应用可能落在“直接 / 未知”。更新已有外部投稿或发送新推广内容应单独获得授权。

## 会话与行为

- 签名 `qyt_visit` Cookie 保存随机会话标识和来源，HttpOnly、SameSite=Lax，HTTPS 下设置 Secure；不包含 IP。有效期 30 分钟，成功浏览公开页面时续期；接口响应不回写来源 Cookie，防止慢请求覆盖新的推广来源。只在同一页持续操作超过 30 分钟，后续接口事件可能没有会话。新来源开始新会话，同站跳转和刷新保留原来源。跨设备、禁用 Cookie 等情况不合并。
- 页面请求需成功返回公开 HTML 才计访问；API、静态资源、HEAD、404、站长页面不创建会话。爬虫、脚本、测试设备和服务器自检沿用原有排除规则。
- 来源表只统计所选期间开始的会话，并观察到该期间结束；三个行为列互不依赖。直接下载、上传后转换都可能跳过解析，不应将它们硬套成严格的连续漏斗。
- 成功解析包含缓存命中的成功响应；原有“解析次数 / 成功率”继续排除缓存重复调用，两者不同。
- 转换成功包括 GIF、实况、动态照片和实况打包，不包含自动准备预览视频。后台任务继承创建时的来源，不会被随后打开的新推广链接覆盖。
- 下载行为是直链下载或产物附件请求，沿用同一 IP、同一文件 10 分钟去重；`inline=1` 预览不算下载。它不证明网络传输完成或用户已保存到相册。
- IP 仍只保存 HMAC 摘要。统计中的 IP 去重人数不是账号人数，不要与会话数混用。

来源表与原事件表分开新增，无需改写历史事件。面板明确展示采集起点、所选期间未记录来源的历史事件和没有会话的事件。新表按 `PARSE_VIDEO_STATS_DAYS` 与原统计一同清理。页面携带来源 Cookie 时使用私有重验证缓存，统计 API 使用 `no-store`；不将个人来源缓存给其他访客。

## 真人与自动化清洗

`/stats` 与 `/api/stats` 默认采用 `audience=human`，只展示高可信真人事件；可切换 `unknown`（不确定）、`machine`（自动化）和 `all`（全部原始记录）。趋势、平台、失败原因、任务和来源表使用同一筛选口径；公开状态页的用户成功率也只使用高可信真人。搜索站长报表独立于本站分类，失败链接排错列表仍展示所有已采集分类，不随筛选变化。

“高可信”是证据分类，不是身份认证，也不是经过标注校准的概率。IP 去重不是自然人数，同一 IP 可以同时有真人和自动化请求；不能将各分类人数相加。未知流量不默认认作真人，快捷指令、小程序和直接 API 调用继续正常工作，没有足够浏览器交互证据时列入不确定。

公开页面通过自托管 ES 模块，在可见页面收到可信指针、键盘或粘贴事件时，向 `/api/visit` 发送三个布尔标记：可见、发生交互、浏览器是否声明由自动化控制。不发送输入、剪贴板、按键、坐标、屏幕参数或完整 URL。服务端校验同源 Origin、限流和已有签名 `qyt_visit` 会话，签发与会话绑定、有效期 30 分钟的 HttpOnly / SameSite=Strict `qyt_activity` Cookie。信号异步落盘，可关联打点到达前已发生的同会话事件和后台任务，页面端失败不影响工具操作。

已知自动化 UA 优先于交互 Cookie，自动化声明不能靠后续打点改回真人。初始异常规则为同会话 60 秒内至少 20 次非缓存解析，归入不确定并给出原因；不统计任务轮询、下载分片、缓存重放和广告请求。不因缺失 Referer、旧系统、VPN 或没有重新加载缓存资源单独判为机器人。主动交互、Cookie 和浏览器属性都可能被仿造，不能独立证明真人。

`event_audience` 旁表保存事件与来源事件的对应关系、分类、固定原因码、会话和规则版本；`audience_sessions` 保存会话证据。原始 `events` / `traffic_events` 不因清洗删除或改写。日常过期清理同步移除旁表，避免 SQLite rowid 复用错误继承分类。站长测试、服务器自检和原有爬虫排除规则继续生效，未被原采集保留的请求不会被清洗程序补造出来。

### 回溯全部现存历史

使用原站点的数据目录与签名密钥运行下列命令。程序仅从本地读取 Nginx combined 格式日志，支持重复 `--log` 和 `.gz`；原始 IP 在内存中转换成原统计使用的 HMAC，不写入旁表，不上传日志。

```bash
# 默认只读预览；域名应包含站点实际使用的主域名与 www 域名
python scripts/clean_stats.py --database data/stats.db \
  --log /path/to/access.log --log /path/to/access.log.1.gz \
  --host example.com --host www.example.com

# 核对预览后应用，先自动创建包含 WAL 的 SQLite 在线备份，再事务性写入分类
python scripts/clean_stats.py --database data/stats.db \
  --log /path/to/access.log --host example.com --host www.example.com --apply
```

程序处理数据库中全部现存事件：同 IP、同 UA 的页面、JS 模块与同源主动接口在 30 分钟内形成访问链路时，归为高可信历史访问；明确自动化客户端归为自动化。仅加载页面或 JS、日志缺失和多个客户端关联不明时保留为不确定。历史日志没有交互标记，不能补造交互或来源；异步任务及下载只在同一时间段唯一可关联时继承分类，异常解析节奏会降为不确定。

事件与日志按秒级时间附近保守匹配，存在多种 UA 时不强行选择；清洗不根据整个 IP 批量删除。重复应用更新历史分类，不覆盖任何已有在线分类，包括尚未证明真人的在线记录；快照后新增或被过期清理替换的事件不会套用旧分类。无日志也可以运行，旧记录全部标为不确定。历史侧表仅处理快照范围，新流量由在线模块持续分类。

备份路径为 `stats.db.before-audience-<UTC时间>`，权限 0600；应用后可对比原始事件总数、三类数量与 `PRAGMA integrity_check`。恢复需先停止应用，使用 SQLite backup API 将备份恢复到原库，再启动应用；不要在写入中的 WAL 数据库上直接覆盖文件。备份含原始统计，恢复窗口结束后由站长清理，避免超出原有保留期限。

规则参考：[isbot](https://github.com/omrilotan/isbot)、[Matomo Device Detector](https://github.com/matomo-org/device-detector)、[BotD](https://github.com/fingerprintjs/BotD)、[BOTracle](https://arxiv.org/abs/2412.02266)、[Shy Guys](https://arxiv.org/abs/2603.28546)。只采用与本站相符的信号，未引入第三方运行时或远程分类服务：通用名单中 `news` 会命中头条内置浏览器，Android Build 编号也可能被误当爬虫标识，不能照单全收。上线前用隔离样本检查误伤，论文与第三方平台的准确率不能当作本站准确率。

## 每周复盘

固定比较两个完整周：先看各搜索平台的展现、点击、CTR 和重点页面，再看本站同来源的会话、成功解析会话、下载会话和转换成功会话。推广按 `source / medium / campaign` 分开，保留链接和发布时间。搜索引擎不披露的匿名查询、受导出行数限制的关键词明细不能当作全站总数。

本地验证使用隔离数据库、离线平台替身和浏览器模拟来源，不读取生产统计或账号 Cookie。上线后应通过正常浏览器打开一个独立测试活动链接，完成实际操作，再核对对应来源行；`/test` 模式和脚本请求被排除，不能用于验证正式采集数量。

参考：[Google Search Analytics API](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)、[Bing 搜索表现](https://www.bing.com/webmasters/help/search-performance-c680da36)。
