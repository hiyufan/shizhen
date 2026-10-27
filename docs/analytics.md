# 搜索表现与推广来源

`/stats` 的访问量和来源统计使用同一个 `PARSE_VIDEO_STATS_TOKEN` 开关与鉴权。来源数据随正常页面请求记录，不需要第三方脚本；部署新版本后开始采集，旧 `events` 保持不变，不能从历史 IP 或页面路径推测来源。

## 先看哪些数字

| 数据 | 在哪里看 | 口径 |
| --- | --- | --- |
| 搜索展现、点击、CTR、平均排名 | Google Search Console、百度搜索资源平台、Bing Webmaster 的效果报表 | 搜索引擎报告；站内日志不能推算未点击的展现或完整搜索词 |
| 搜索带来的会话和落地页 | `/stats` → 搜索带来的访问 | 根据入站 Referer 或明确标记的 organic 来源识别 |
| 推广来源和活动 | `/stats` → 搜索与推广来源 | UTM 优先，其次是外部引荐域名 |
| 成功解析、下载行为、转换成功 | 同一来源或落地页的行 | 按访问会话去重，分母是所选期间内开始的会话 |

三家站长平台的时间范围、时区和搜索类型要分别记录。Google Search Console 的 API 日期按太平洋时间解释；站内统计使用浏览器时区。对比时选完整日期，并保留平台显示的数据截止日期，不将尚未出齐的当天数据与完整一天比较。

搜索点击和本站会话是两个口径：浏览器缓存、来源被应用隐藏、Cookie 被禁用、重复点击、机器人过滤和数据延迟都会产生差异。没有报表时写“未接入”，不能用 0 占位，也不能把三家平均排名直接取平均。查看报表需要对应站点的读取权限，站点验证文件本身不等于报表访问授权。

官方入口：[Google Search Console](https://search.google.com/search-console)、[百度搜索资源平台](https://ziyuan.baidu.com/)、[Bing Webmaster Tools](https://www.bing.com/webmasters/)。

## 推广链接

在 `/stats` 展开“生成推广链接”，选择站内落地页并填写来源、媒介和推广标记。地址使用配置的 `PARSE_VIDEO_SITE_URL`，不包含统计口令。分享给用户的是生成的公开工具页链接。

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

## 每周复盘

固定比较两个完整周：先看各搜索平台的展现、点击、CTR 和重点页面，再看本站同来源的会话、成功解析会话、下载会话和转换成功会话。推广按 `source / medium / campaign` 分开，保留链接和发布时间。搜索引擎不披露的匿名查询、受导出行数限制的关键词明细不能当作全站总数。

本地验证使用隔离数据库、离线平台替身和浏览器模拟来源，不读取生产统计或账号 Cookie。上线后应通过正常浏览器打开一个独立测试活动链接，完成实际操作，再核对对应来源行；`/test` 模式和脚本请求被排除，不能用于验证正式采集数量。

参考：[Google Search Analytics API](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)、[Bing 搜索表现](https://www.bing.com/webmasters/help/search-performance-c680da36)。
