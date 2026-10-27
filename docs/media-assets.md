# 公开动态素材：实现范围与后续研究

2026-10-26。优先围绕「取出动画 → 下载原格式 → GIF / 实况 / 动态照片」提供完整流程。下面是实现验证与候选方向，不代表搜索量、独占功能或 Star 增长预测。

## 已接入

| 来源 | 入口和边界 | 验证重点 |
| --- | --- | --- |
| Steam 点数商店 | `/steam-assets`；单个 `/reward/` 物品，公开头像、头像框、贴纸和背景素材 | 精确查询 definitionids 并核对物品与 appid；动画可能在 small 字段，large 可能只是静态预览；下载保留 GIF / APNG / MP4 / WebM |
| HoYoPlay | `/hoyoplay-wallpaper`；选择原神、星穹铁道、绝区零或崩坏 3，读取当前国服启动器公开背景 | 按 game_id 与 biz 匹配游戏；图片去重，视频按实际返回展示；不提供历史版本档案 |
| Apple Music | `/apple-music-cover`；歌曲、专辑、歌单的公开网页封面 | 只读取作品 header，不能回退到列表中的其他歌曲；静态图与公开动态视频分别下载，不请求歌曲音频 |
| 鸣潮 | `/wuthering-waves-wallpaper`；当前国际服启动器背景 | 从当前配置的 background 版本读取资源；视频与首帧分别下载；没有视频时只显示图片；不提供历史档案或国服素材 |
| 米游社 | `/miyoushe-images`；单篇公开帖子的正文图集 | 带站点 Referer，核对 post_id；保留正文顺序、原始地址与 GIF 格式，不混入头像、推荐图或单独封面；不包含 HoYoLAB、评论和主页批量获取 |
| B站装扮与收藏集 | `/bilibili-assets`；完整装扮或收藏集详情链接 | 装扮需 id，收藏集需 act_id + lottery_id；只读取公开图片与视频，不读取私有缓存或解锁账号权益；专用动画包不作为普通媒体处理 |

Apple Music 动态封面经安全 HTTP 客户端读取 HLS，校验 Apple 媒体主机、有限播放列表、时长、分段数量、字节范围与总下载量；拒绝加密媒体。先在本地拼接视频，再让 FFmpeg 读取本地文件，不把远程播放列表交给 FFmpeg。

Steam 透明动画另存转换用的透明中间文件，GIF 从该文件生成；预览与实况使用 H.264。APNG 的默认静态预览不计入动画帧，帧时长按毫秒保留。缓存登记与清理同时覆盖透明中间文件。

B站套装里的独立素材放在 `VideoInfo.assets`，不塞进同一视频的 `formats`，也不借用原生实况的 `live_photo_url`。素材及预览地址均经现有签名、代理与准备流程；收藏集 `video_list` 中的多个地址是 CDN 备份，只展示一份视频。

新增三项的真实验证（2026-10-26）：

- 鸣潮国际服当前配置返回 MP4 与 WebP 首帧；米游社帖子 `41214610` 返回 2 张 PNG 和 1 张 GIF。
- B站装扮 `34814` 返回 5 份素材，完整套装 `42193` / `66359` 分别返回 39 / 30 份；收藏集 `act_id=111&lottery_id=15` 返回 16 张图片和 7 段视频。数量随平台更新可能变化。
- 鸣潮视频、米游社 GIF、收藏集视频均经真实下载，分别转换为 GIF、实况包与动态照片，共 9 组产物。验证 GIF 多帧、JPG/MOV 标识配对、动态照片 XMP 与内嵌 MP4；未验证实体手机导入。测试使用项目依赖自带 FFmpeg，本机系统版本缺少 libx264。
- 中英文工具和教程在 320 / 390 / 1440px 检查布局，真实下载 PNG / MP4、GIF 与实况转换通过；另用延迟响应检查等待、清空、换链接与旧结果隔离。
- 米游社图片 CDN 在后续复测中出现 `ConnectTimeout` / `ConnectError`，预览重试仍可能失败；再次复测后，三张图片在 320 / 390 / 1440px 均完整加载。原图下载和 GIF 转换确已成功，但当前出口有波动。保留平台原始地址及现有重试，不将某次成功视为长期可用保证。
- 既有平台烟测：抖音和 X 返回媒体；B站普通视频遇到 HTTP 412，小红书旧短链跳转首页，未验证成功。与新装扮接口的真实成功分别记录，不用离线测试替代平台烟测。

资料入口：

- [Steam 官方点数商店素材规范](https://partner.steamgames.com/doc/marketing/pointsshopitems?l=schinese)：头像、头像框、贴纸与背景的实际格式。
- [HoYoPlay Launcher Background](https://github.com/UIGF-org/HoYoPlay-Launcher-Background)：已有启动器背景提取项目，说明不是无人涉及的方向。本项目使用独立解析与现有安全下载封装。
- [Apple Music Artwork Search](https://github.com/NopXx/apple-music-artwork-search)：已有封面工具；本项目侧重与 GIF、实况转换的衔接。
- [鸣潮启动器公开请求说明](https://gist.github.com/DynamiByte/d839bf9f671c975b6666d0f6e6634641)：据此核对当前国际服配置与背景资源，未复制第三方下载器代码。
- [米游社文章接口资料](https://github.com/UIGF-org/mihoyo-api-collect/blob/main/hoyolab/article/article.md)：核对正文图片与帖子身份字段。
- [B站装扮与收藏集接口资料](https://github.com/BACNext/bilibili-API-collect-backup/blob/master/docs/garb/lottery.md)：核对装扮字段及 `lottery_home_detail`；旧 `act/item/list` 在本次探测中返回空对象，未用于实现。

## 匿名 Telegram 与视频号：2026-10-27 续研结果

| 方向 | 已实现范围 | 真实验证与限制 |
| --- | --- | --- |
| Telegram | `/telegram-stickers`；接受 addstickers 或 Stickers.wiki 单包链接，读取已公开收录的 TGS / WebP；动态 TGS 转透明 GIF、实况与动态照片 | HotCherry 目录返回 31 项；下载首个 TGS 8244 字节，并生成三种产物。图片预览来自对应 WebP。目录不是 Telegram 实时接口，未收录或版本不同的包不保证可用，不需要 Bot 或账号凭证 |
| 视频号 | `/wechat-channels`；官方匿名 `shortUri` 预览核对作品，BugPk 公益接口补充匹配视频；视频与封面分别下载，支持三种转换 | 两条 JPEG 封面均下载验证；`AYtCGrpUgl` H.264 视频完整下载 6,316,416 字节，约 41.52 秒、1024×576、30 fps、有音轨；H.265 备用文件 4,279,737 字节，尺寸与时长一致。已生成 GIF、实况包、动态照片并验证配对标识和附加视频。不发送账号凭证；公益接口不可用时保留封面，不保证覆盖全部作品 |

TGS 使用 `stickers` 可选依赖 `rlottie-python`，Docker 镜像包含该依赖。原生渲染在独立子进程中执行：压缩文件 ≤256 KB，解压 ≤4 MB，≤512×512、≤60 fps、≤600 帧、≤10 秒及总像素限制，拒绝外部图片和字体；子进程有 30 秒墙钟超时，POSIX 下另有 CPU / 内存限制，取消会终止并回收进程。透明中间文件继续复用现有 GIF 流程。原始文件下载不要求安装渲染器，依赖自身许可证随安装包保留。

继续复核了视频号的匿名路径：

- 官方分享页当前直接调用 `https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`，请求 `{baseReq: {generalToken: ""}, shortUri: "分享编号"}`。不需要先经元宝换取元数据。
- 与真实浏览器请求对比后，桌面匿名请求能返回元数据和封面，测试样例没有 `videoUrl` / H.264 / H.265 视频地址。手机微信 UA 请求在当前环境返回 401；未采用它作为兜底。
- 把返回的 dynamicExportId 重新请求，仍未取得可播放视频；移除封面缩略图参数，实际文件头仍为 JPEG。没有将这些方法当成视频下载方案。
- 元宝匿名解析请求返回 401；上游公开 Worker 的 API 当前也返回 401。二者都依赖凭证，因此没有接入本工具，也没有自动寻找、读取或复用其他人的凭证。
- BugPk 官方文档公布 `https://api.bugpk.com/api/wxsph?url=分享链接`，无需 Key，QPS=1。实际匿名调用返回的视频标题和作者与官方预览一致；腾讯媒体文件包含正常 MP4 文件头，完整下载可解码。已接入作为备用，默认开启，可通过 `PARSE_VIDEO_CHANNELS_PUBLIC_FALLBACK=0` 关闭。运营方可能改变限流或停止服务，不作永久可用承诺。
- 接口标称 1080p 的实际文件是 1024×576，不据标签推断分辨率或宣传高清。只接受腾讯媒体 HTTPS 地址；不匹配的作品、受限作品与接口错误均不作为视频成功。
- 另一家 Yuafeng 文档虽称 Key 可选，实测返回缺少 Key 的 403，未接入。

可复现的只读探测：

```bash
python scripts/probe_media_candidates.py telegram https://t.me/addstickers/HotCherry
python scripts/probe_media_candidates.py channels https://weixin.qq.com/sph/AYtCGrpUgl
python scripts/probe_media_candidates.py channels /path/to/response.json
```

脚本仅报告目录条目、媒体字段和布尔状态，不把元数据成功当作完整文件下载成功，也不回显媒体签名、令牌或解密密钥。

资料：[Hot Cherry 公开目录](https://stickers.wiki/telegram/hotcherry/)、[Telegram TGS 格式](https://core.telegram.org/api/stickers)、[rlottie-python](https://github.com/laggykiller/rlottie-python)、[官方分享页](https://weixin.qq.com/sph/AYtCGrpUgl)、[BugPk 公益接口及限流说明](https://api.bugpk.com/)、[视频号接口文档](https://api.bugpk.com/doc-wxsph.html)、[公开分享样例来源](https://www.haqjp.gov.cn/col/1835_433615/art/m/17828352/1783416101141vN6k4LEV.html)、[需凭证的参考 Worker](https://github.com/ltaoo/wx_channels_download/blob/main/internal/workers/sph/worker.js)。

## B站普通视频：2026-10-27 修复验证

- 旧 `/x/web-interface/view` 返回 412 时，改试新版 `/x/web-interface/wbi/view`，仅接受匹配原 BV / AV 编号的成功响应；BV 号主体区分大小写。
- 若作品详情仍被拦，只有分 P 列表与播放接口均返回可用媒体时才继续，保留目标分 P 和时长；不把失败的播放结果当作成功。
- 当前出口上，部分 CDN 普通 GET 返回 567，而 `Range: bytes=0-` 返回完整 206。视频直链与 DASH 下载均使用这一请求方式；共享下载器校验 Content-Range 覆盖完整对象，并拒绝分段或截短文件。
- 下载任务恢复 `bilibili` 平台上下文，以复用用户已有国内代理或中继；本次验证未配置账号 Cookie 或专用代理。
- `BV1GJ411x7h7`、`av170001` 均恢复解析。第一条完整合并 720p MP4 为 51,973,341 字节，FFmpeg 检测 1280×720、25 fps、约 212 秒且带音轨。仅解析返回地址不作为下载成功的依据。
- 用上述完整文件截取真实片段，已生成 GIF、实况包与安卓动态照片；浏览器预览实际播放，未完整验证所有高清档位的下载。

## Discord 公开贴纸：2026-10-27 接入验证

- 工具 `/discord-stickers`，教程 `/guide/discord-stickers-to-gif`。接受公开贴纸包商店 SKU、包编号链接，以及对应的 `stickers/编号.png` / `.json` / `.gif` 素材链接。页面提供两个真实包的快捷示例。
- 官方 `/api/v10/sticker-packs` 目录匿名返回 HTTP 200，本次共 14 个包。单包及单贴纸端点实测返回 401，因此实现只匹配匿名目录中的包 ID、SKU 或贴纸 ID，不请求账号 Token，也不访问服务器或消息接口。目录之外的素材明确返回不支持。
- Wumpus Beyond 包返回 31 项；首个 APNG（`1228092333061443654`）完整下载 984,455 字节，320×320、50 帧、0.5 秒。Mallow The Rascal 包返回 30 项；首个 Lottie（`796140620111544330`）JSON 为 291,805 字节，320×320、60 fps、2 秒。计数随公开目录变化。
- 两类真实素材均通过签名代理下载、服务端准备及 GIF / 实况 / 动态照片转换，共 6 组产物。GIF 验证多帧及透明像素；实况包验证 JPG / MOV 配对标识；动态照片验证 XMP 长度与附加 MP4 可解码。尚未验证实体手机相册导入。
- Lottie JSON 与 TGS 共用隔离原生渲染子进程；JSON 上限 1 MB，沿用动画尺寸、帧率、帧数、时长、总像素与外部资源限制。Lottie 的 PNG 预览地址实测返回 404，因此结果显示转换提示，不制造错误图片地址。转换后展示实际动画。
- 真实 JSON 下载发现 CDN 使用 Brotli 传输压缩。共享代理原样传输响应体时遗漏了 `Content-Encoding`；已补齐响应头透传，离线压缩下载回归测试与真实 JSON 下载均通过。
- 当前逐项下载与转换，不提供整包 ZIP；不改变 Discord 内的贴纸发送权限，不把下载等同于取得 Nitro 或服务器权益。

资料：[Discord 官方贴纸对象与目录接口](https://docs.discord.com/developers/resources/sticker)、[Discord API 请求规范](https://docs.discord.com/developers/reference)。

## Pixiv：2026-10-27 匿名接入与验证

结论：可以把 Pixiv 主站的主要公开内容做成统一解析入口，范围包括插画、多页漫画、Ugoira、小说、系列、作者作品目录、标签 / 榜单和 Collections。公开单篇的原图与 Ugoira 转换已实测；目录类主要验证了列表读取，不能据此声称全部作品已下载。已注册 Pixiv 与 Sketch 解析器，并接入原图 / 小说、Ugoira、Sketch 三个工具页、两篇教程和中英文入口；没有采用账号凭证。不能承诺任意私密、年龄受限或已失效链接均可下载。本文“接入”指当前代码，不代表已经推送或部署生产。

### 正式接入验证

- 入口 `/pixiv-download`、`/pixiv-ugoira`、`/pixiv-sketch`，首页卡片、页脚互链、站点图、JSON-LD、中英文和自托管字体已同步。相关教程为 `/guide/pixiv-original-images` 与 `/guide/pixiv-ugoira-to-gif`。
- Pixiv 公开插画、多页漫画、作者、漫画 / 小说系列、带上传插图的小说、Collections、标签与榜单均通过真实解析。作者 `197119` 第一页 30 项、第二页 15 项；漫画系列和小说榜单第二页返回不同作品。搜索响应含广告占位，过滤后不作为作品。标签按插画、漫画、Ugoira 使用对应查询，小说与集合分别查询。
- Ugoira `151302250` 经正式 `/api/parse`、签名代理和 `/api/prepare` 下载 5,044,403 字节帧包，CRC 通过；服务端输出 MP4 为 569,219 字节、1000×1000、2 秒。其 17 个显示时间点（含末帧结束占位）为 `0,120,250,370,500,620,750,870,1000,1120,1250,1370,1500,1620,1750,1870,2000 ms`，与逐帧累计时长完全一致。
- 正式接口另生成 GIF 229,886 字节、实况包 593,486 字节、动态照片 594,259 字节；验证多帧、JPG / MOV 标识配对、XMP 和内嵌视频。网页另实际完成 GIF 转换、视频播放、时序 JSON 与小说 TXT 下载；未验证实体手机导入。
- 帧包下载上限 32 MiB（且不超过服务器源文件限制），解压合计 ≤128 MiB、单帧 ≤8 MiB、单帧 ≤800 万像素、总像素 ≤1 亿，转换 ≤600 帧 / 30 秒。检查重复文件、路径、CRC 与元数据精确匹配，不直接提取平台路径；解码在可取消线程执行，退出后才清理临时目录，编码复用现有受控 FFmpeg。较长动画在有界元数据范围内保留原始帧包和时序下载，隐藏转换入口。
- Sketch `@nicoby` 目录与单帖 `1397454509844689741` 均由正式解析器返回，图片原地址保留。Collections 目前实际样本仍为作品与文字；外链的官方类型 `ExternalUrl` 和 `url` 字段按当前官方网页代码核对，使用离线响应验证，不自动抓取外站。
- 桌面 1440px、手机 390 / 320px 检查入口、成功、目录、小说、失败、清空、翻页和换链接，无横向溢出或控制台错误；延迟请求在清空后不会恢复旧结果。浏览器插件不可用，使用现有 Playwright，截图与临时验证脚本保存在仓库外。

### 真实样例

| 作品 ID | 帧包完整字节数 | 帧数与实际尺寸 | 逐帧时长 / 总时长 |
| --- | --- | --- | --- |
| `151301221` | 16,931,691 | 34 帧，1260×1080 | 每帧 80 ms / 2.72 秒 |
| `151278901` | 16,845,135 | 72 帧，811×1080 | 每帧 80 ms / 5.76 秒 |
| `151268999` | 3,279,361 | 18 帧，1024×768 | 每帧 125 ms / 2.25 秒 |
| `151302250` | 5,044,403 | 16 帧，1000×1000 | 120 / 130 ms 交替 / 2 秒 |

- 使用官方网页接口 `GET https://www.pixiv.net/ajax/illust/作品ID` 核对作品身份，再从 `/ugoira_meta` 取得 `src`、`originalSrc`、`frames[].file` 和 `frames[].delay`。四条作品的 `illustType=2`、`xRestrict=0`，匿名请求均成功；没有使用 `PHPSESSID`、OAuth 或代理解析平台。
- 四份 ZIP 均完整下载，中央目录、CRC、文件名与元数据帧列表一致，所有帧可读取。原型限制下载 <30 MB、解压合计 <128 MB、单帧 <8 MB，不使用平台文件名直接提取到任意路径。正式实现仍需接入现有任务配额、取消、尺寸和总像素限制。
- 按元数据逐帧时长生成本地 FFmpeg 清单，先制成研究用 MP4，再通过现有本地上传与转换接口生成 GIF、实况包和动态照片，共 12 组产物。GIF 验证多帧；实况验证 JPG / MOV 标识配对；动态照片验证 XMP 长度和附加 MP4 可解码。这 12 组是前期研究产物；正式网页接入另有下述真实验证。
- 对交替帧时长样例另用 FFprobe 校验实际显示时间戳：`0, 120, 250, 370, 500, …, 1870, 2000 ms`，与元数据累计值一致。编码需要保留末帧时长与毫秒时间基，不能简单固定为平均帧率。研究 MP4 在结尾保留同一末帧的结束占位；原始帧包与时序 JSON 保持原数据。
- `originalSrc` 不等于作者完整原始尺寸。`151301221` 的 ZIP 实际帧为 1260×1080，作品信息尺寸为 1400×1200；官方返回的独立原始首帧与最后一帧均成功下载，实际是 1400×1200。界面区分帧包与图片，不按文件名标注未验证的原始尺寸；转换按实际文件校验，不能仅按 URL 中的 `1920x1080` 宣传画质。
- 普通插画 `44525295` 的作品及 `/pages` 接口匿名可用，原图下载 1,133,824 字节、717×1000。其 `illustType=0`，`/ugoira_meta` 返回 404 且明确说明不是 Ugoira；必须先识别作品类型。

### 全链接覆盖调查：主站内容与入口

这里的“统一解析”指识别链接类型后返回相应内容：单篇媒体供下载和转换，目录供翻页选择，小说供文本导出。识别成功、目录读取成功、媒体下载成功分别记录；不把 HTTP 200、缩略图或推荐作品当成目标内容成功。以下结果来自 2026-10-27 当前出口的匿名请求，保留环境通用网络代理，未配置 Pixiv 专用代理、Cookie 或 OAuth。

| 类型与链接形态 | 本次真实验证 | 可接入范围与边界 |
| --- | --- | --- |
| 插画与多页插画：`/artworks/{id}`，可带语言前缀 | `44525295` 原图完整下载，见上节 | 根据作品类型与 `/pages` 读取全部页面；多图不能只保留封面 |
| 漫画：同样使用 `/artworks/{id}` | `151271322` 返回 14 页原图地址，首尾两页完整下载，分别为 2,127,142 字节 / 1020×1447 与 744,316 字节 / 1000×1423 | 按正文页序展示；未下载该作品中间 12 页，不宣称整本下载已验证 |
| Ugoira：同样使用 `/artworks/{id}` | 四份 ZIP 与 12 组转换产物，见上节；另读到 PNG / GIF 原始帧作品 | 原始图片、播放帧包与转换产物分别标注格式、尺寸和时序 |
| 作者及分类：`/users/{uid}`、`/artworks`、`/illustrations`、`/manga`、`/novels` | 作者 `197119` 的公开目录含 42 个插画 ID、3 个漫画 ID；作者 `77460883` 含 84 个小说 ID；详情返回头像等资料 | 分类页归一为作者目录，再按类型筛选；作品逐项解析、分页和限制批量大小，不能把整个作者目录当一条视频。头像、背景仅验证字段，未验证下载 |
| 漫画系列：`/user/{uid}/series/{sid}`，这里是单数 `user` | 系列 `21859` 返回总数 112、首批 12 个作品编号与序号 | 保留系列顺序与翻页；响应还含其他系列，必须匹配目标系列及所属作者，不能取推荐项 |
| 单篇小说：`/novel/show.php?id={id}` | `29473164` 匿名返回 28,680 字符正文；封面下载 34,346 字节、427×600。`16422450` 返回正文、1 张上传插图及 4 处 Pixiv 图片引用；上传插图下载 1,021,455 字节、1300×1300 JPEG | 可以做 TXT / HTML 导出，并保留章节、换页、注音及图片引用；EPUB 是后续实现选项，尚未生成。返回封面尺寸不是已验证的原始封面尺寸；未下载被引用的全部作品 |
| 小说系列：`/novel/series/{sid}` | 系列 `15208260` 的详情和章节目录均成功，目录含 6 章 | 先选章、再取得正文；未逐章下载或导出整个系列 |
| 标签与搜索：`/tags/{tag}/artworks` 等分类入口 | 安全模式标签“初音ミク”的作品搜索返回首批 60 项和总数 | 支持当前查询条件与翻页；这里只验证作品搜索第一页，小说 / Collections 搜索筛选、历史搜索和付费排序不能直接视为已验证 |
| 排行榜：`/ranking.php`、`/novel/ranking.php` | 插画接口中的漫画、Ugoira 日榜各返回 50 项；小说日榜 JSON 接口返回结构化结果 | 保留榜单日期、模式、内容分类与页数；小说 HTML 入口即使加 `format=json` 仍返回 HTML，不能用插画榜方式直接套用 |
| Collections：`/collections/{id}` | 公开搜索返回目录；3 个详情均匿名成功，分别有 19、6、60 个作品条目及各 1 个文字条目 | 这是混合内容集合，并非收藏夹；作品、文字、外链分别处理。作品条目再解析对应内容，不把集合缩略图当原图，也不自动抓取外链全部站点 |
| URL 限定作品：`/artworks/unlisted/{opaque}` | 使用 gallery-dl 已公开的完整测试链接读取成功，对应作品 `85017704`；原图完整下载 2,180,398 字节、1500×2221 PNG。未猜测或枚举链接 | 需要保留完整限定链接，不能仅取普通作品 ID 后改走另一接口；示例限定码未写入本文。作者更新限定链接后旧链接可能失效 |
| 公开收藏夹：`/users/{uid}/bookmarks/artworks`、`/bookmarks/novels` | 两位作者的插画收藏接口返回 400；作者 `173530` 的网页返回 200，但实际状态为 `noLoginIndex`，未提供收藏列表 | 官方允许其他人查看公开收藏，但当前匿名路径需登录，暂不承诺可用；未验证小说收藏匿名读取。私密收藏不在范围内 |
| 关注动态、个性化推荐及账号页面 | 关注动态与发现接口本次返回 400 | 识别为账号相关入口并说明边界，不把失败伪装成空列表；不接账号凭证 |

作者与系列的全部目录枚举、分页到末尾和逐项下载尚未完成；目录总数不是已下载数量。年龄受限、My pixiv、私密、删除、封禁、付费及 Premium 专属功能不作匿名可用承诺。URL 限定作品与这些权限类别分开判断：官方说明持有完整链接的人可查看，且作者可以更新链接。

Collections 官方说明还允许小说、Ugoira、其他 Pixiv 服务链接、任意外链和文字。本次三个集合实际只包含 `Work / illust` 与 `Text`，没有验证小说、外链及其他服务条目的真实响应结构；不能据文档列举就声称这些条目均已解析成功。集合编号达到 20 位，前后端都应按字符串处理，不能用 JavaScript `Number` 存储。

### 短链接、旧链接和 CDN 地址

- 已验证 `/i/151301221`、`/u/197119`、`/n/29473164` 以 301 跳转到对应的作品、作者和小说入口；旧 `member_illust.php?mode=medium&illust_id=151301221` 同样跳转作品页。
- `https://pixiv.me/del_shannon` 匿名跳转作者 `173530`。正式识别器仍应逐跳校验目的主机，不能把任意外链当作 Pixiv 入口。
- 语言前缀、分享参数和锚点可以归一；作者分类、系列、搜索筛选与页码要保留。旧 `member.php?id=...`、移动站和 `pixiv://...` 应作为兼容入口补测，本次未逐一验证。
- `i.pximg.net` 的图片 / 帧 ZIP 直链可作为单个文件入口；需要“整篇作品”时，通过可靠编号回查官方元数据。裁切图不能靠替换目录或后缀冒充原图；2026 年地址还可能含 `作品ID-哈希_p页码`，不能只支持旧文件名。
- `101003492` 的独立原始首帧实际下载为 20,066 字节、1210×1210 PNG；`108469527` 为 19,341 字节、300×300 单帧 GIF，但二者 `/ugoira_meta` 都返回 JPEG 播放帧列表，分别为 6、30 帧。此前四条 JPEG 原始帧作品不足以覆盖全部格式。这里只下载了这两条作品各自的首帧；当前研究脚本只检查播放帧元数据，不负责完整原始逐帧下载。

### 相关服务不混同主站

| 服务 | 当前调查结果 | 后续范围 |
| --- | --- | --- |
| Pixiv Sketch：`sketch.pixiv.net` | `@nicoby` 的公开目录匿名返回 10 条帖子；其中一条的原图完整下载 656,863 字节、1479×800 | 已接入公开作者目录与单帖原图；目录第一页与数字 ID 单帖实测成功，未验证全部短链与翻页到末尾 |
| FANBOX：`fanbox.cc` | 存在公开帖和支持者权限等不同内容；本次未做真实下载验证 | 独立解析器，只考虑可匿名读取的公开帖，不承诺付费 / 支持者内容 |
| pixiv Comic、BOOTH、pixivision、百科等 | 域名、内容对象和产品用途不同，本次未逐项验证 | 不能因属于 Pixiv 生态就宣称支持所有链接；分别评估公开图片、文章引用与商品预览，付费下载与账号功能另属范围 |

### 实现约定与后续扩展

1. 统一识别 Pixiv 主站入口，先实现公开单篇插画、多页漫画与 Ugoira，包括短链归一和完整 URL 限定链接。解析层保存作品身份、预览、页序、ZIP 地址与逐帧时长；ZIP 不放进 `video_url` 或同视频清晰度的 `formats`。
2. 动画提供帧 ZIP 与时序 JSON 下载，二者一起才能还原播放；转换层校验 ZIP 路径、重复条目、CRC、帧数、压缩与解压体积、实际像素和时长，再复用现有 GIF / 实况 / 动态照片任务。独立原始逐帧图片按真实 PNG / GIF / JPEG 格式处理，不从播放帧格式推断。
3. 作者、漫画 / 小说系列、标签、榜单与 Collections 先返回有限分页目录，让用户选择具体作品。保留系列顺序，去重同一作品，限制单次解析和下载量，支持取消；不递归遍历整个站点，也不让单项失败抹掉其他结果。
4. 小说使用独立文本结果，区分正文、封面、上传插图和引用作品，再做文本导出；媒体结果继续使用 `VideoInfo`，不把小说正文或作者目录塞进视频 URL / 清晰度字段。正式修改共享返回契约前补齐旧接口与前端兼容设计。
5. 原始逐帧图片可作为更高尺寸选项；本次没有完整下载任一作品全部独立原始帧。受限、已删除或无法匿名访问的内容返回对应原因，不接账号凭证。每类目录能力单独验证后再宣传覆盖，不能因为链接已识别就显示“下载成功”。
6. 搜索入口先围绕可验证交付的「Pixiv 原图下载」「Pixiv 漫画下载」「Ugoira 转 GIF」「Pixiv 转实况」，其后才是目录与小说工具。已有 gallery-dl、PixivUtil2 等成熟工具，本项目差异放在网页中选择、截取和转换。三个工具入口与两篇教程已接入代码；搜索量与新增 Star 效果尚无数据。

可重复的元数据探测（不下载媒体，也不把 ZIP 文件名当成实测分辨率）：

```bash
python scripts/probe_media_candidates.py pixiv https://www.pixiv.net/artworks/151302250
```

资料：[Pixiv 官方 Ugoira 说明](https://www.pixiv.help/hc/en-us/articles/235584628-What-are-Ugoira)、[Pixiv 官方 ZIP 播放器](https://github.com/pixiv/zip_player)、[gallery-dl 的 Pixiv 解析实现](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/pixiv.py)、[PixivUtil2](https://github.com/Nandaka/PixivUtil2)。本原型独立编写，没有复制上游解析器或转换器。

类型与边界参考：[Collections 官方定义](https://www.pixiv.help/hc/en-us/articles/48871923503897-What-are-collections)、[URL 限定作品说明](https://www.pixiv.help/hc/en-us/articles/900002308986-What-is-URL-restriction)、[公开与私密收藏说明](https://www.pixiv.help/hc/en-us/articles/235646967-What-are-Bookmarks)、[小说系列说明](https://www.pixiv.help/hc/en-us/articles/235584188-I-want-to-post-a-novel-as-a-series-like-episode-1-episode-2-etc)、[gallery-dl 的公开 Pixiv 测试链接](https://github.com/mikf/gallery-dl/blob/master/test/results/pixiv.py)、[小说插图测试样例](https://github.com/mikf/gallery-dl/blob/master/test/results/pixivnovel.py)。Collections 详情及搜索路径还按当前官方网页 JavaScript 的实际 GET 请求核对，不依据旧资料猜测端点。

## 2026-10-27：下一轮平台与格式机会调查（接入前记录）

本轮在现有平台之外比较匿名可用性、真实素材、已有整合工具与搜索入口。建议先做 **7TV / BetterTTV / FrankerFaceZ 直播表情统一入口**，再做 **B站公开装扮中的 SVGA 动画**；LINE 可作为较容易补齐的公开预览来源。Signal 保留为待解决当前出口问题的候选，Noto 作为补充素材来源。本节保留接入前的调查结论；后续已完成的功能、Signal 出口修复与真实转换结果见[本轮实现与验证](#本轮实现与验证)。

已有 [EmoteDownloader](https://github.com/Daniel2193/EmoteDownloader) 整合 Twitch / BTTV / FFZ / 7TV，[sticker-convert](https://github.com/laggykiller/sticker-convert) 整合多个贴纸平台，[Stickify](https://github.com/n0bitaemon/stickify) 也提供网页贴纸流程。因此不能称为“无人整合”。本项目可争取的差异是公开分享链接、原格式下载、透明动画转换与手机保存的衔接；本轮没有验证任何竞品的全部实际行为，也没有获得关键词搜索量或增长数据。

### 候选顺序与需求依据

| 顺序 | 候选与用户问题 | 本次验证程度 | 可争取的差异与投入判断 |
| --- | --- | --- | --- |
| 1 | 7TV / BetterTTV / FrankerFaceZ：直播表情保存到本地、跨聊天应用使用 | 三站匿名目录和实际文件成功；7TV 样例完成三种转换，FFZ 只验证静态 PNG | 一处识别三站链接，区分静态 / 动态，提供透明 GIF 与限体积导出。主要复用现有图片转换层，新增解析和目录适配；频道与自定义集合尚须另测 |
| 2 | B站 SVGA：赞动画等专用格式无法直接作为 GIF / 实况使用 | 官方样例与 B站真实 `.bin` 均完整下载并读出动画参数，尚未渲染 | 扩展现有装扮解析，减少用户先找文件再找转换器的步骤。需新增有界 SVGA 解析与逐帧渲染，投入明显高于图片来源适配 |
| 3 | LINE Store：动态贴图下载后只显示首帧，或无法在其他应用使用 | 页面公开预览、真实 APNG 与三种转换成功 | 手机网页中选择预览素材并转换；已有网页和扩展支持 APNG / GIF / ZIP，单纯“整包下载”差异有限。先做普通静态 / 动态预览，其他类型分开验证 |
| 4 | Signal：公开包希望保存成普通图片而非只安装到 Signal | 官方协议支持凭公开链接解密；当前环境 TLS 验证失败，未取得清单或贴纸 | 链接本身带包密钥，无需账号 Cookie；仍需完成实际下载、认证解密与格式验证。不能列作已经成功的平台 |
| 5 | Google Noto Animated Emoji：按 emoji 找透明动画、在其他软件中使用 | 官方 WebP 与 Lottie 完整下载；WebP 解码成功 | 官方已有下载，因此宜作为统一素材入口与转换补充，不单独押注增长；动画素材许可为 CC BY 4.0，需保留出处与署名 |
| 后续 | PAG / VAP / Spine：特殊动画转通用格式 | 仅核对官方运行库与已有导出项目，未验证真实转换 | 更偏向开发者工具能力；涉及不同渲染器、资源包与版本兼容，不能因完成 SVGA 就承诺通用支持 |

需求与竞争证据：

- **直播表情**：[7TV 官方](https://7tv.app/)明确使用 WebP / AVIF；[用户询问如何避免 WebP](https://www.reddit.com/r/7TV/comments/1c7zbdg/how_to_downlad_emote_not_as_webp/)及 [iMessage 表情项目的用户讨论](https://www.reddit.com/r/7TV/comments/1h761bx/send_7tv_emotes_on_imessage/)说明格式兼容和转发存在真实需求。这些是个案，不是搜索量。[giveemot.es](https://giveemot.es/)已提供 7TV 图片链接与格式选择，不能把“7TV 转 GIF”本身当作独占功能。
- **SVGA**：[B站 V 家素材维护者](https://github.com/luotianyi-dev/vocaloid-bilibili-stickers)说明已经尝试 PNG 序列与 GIF，但其 GIF 质量不理想。这支持进一步调查透明边缘、遮罩和时序问题，不能推断所有其他转换器都存在同样缺陷。[svga.online](https://svga.online/)也已展示 SVGA / VAP 转换产品，页面出现访问 Token 入口；本轮未测试其转换质量。
- **LINE**：[StampNyaa](https://github.com/MarvNC/StampNyaa)已做桌面下载与跨应用使用；[LINE Sticker Downloader 扩展](https://github.com/Li1vErk/line-sticker-downloader)支持选择下载、APNG 与 GIF；[kelvinct 网页工具](https://kelvinct.com/sticker/)也展示公开包的 APNG / GIF / ZIP。机会是复用本站已有操作流程与手机转换能力，不能称为没有网页方案。
- **Signal**：[用户询问如何导出贴纸文件](https://www.reddit.com/r/signal/comments/1c9dmix)说明安装与保存文件是不同需求；[官方说明](https://signal.org/blog/make-privacy-stick/)明确公开分享 URL 中的包编号和包密钥允许任何持有链接的人查看、解密及安装。已有工具也支持该流程，不属于需要破解账号权限的方向。
- **PAG / Spine**：[Tencent/libpag](https://github.com/Tencent/libpag)提供独立 PAG 渲染库，[SpineViewer](https://github.com/ww-rm/SpineViewer)已有查看、导出及壁纸工具。它们适合后续研究，不以项目 Star 数推断本站的流量或可获 Star 数。

### 匿名真实探测记录

探测保留环境通用网络代理，未使用平台账号、Cookie、Bot Token 或第三方账号凭证。HTTP 通过现有安全客户端，媒体下载设字节上限；临时文件和转换产物位于仓库外。以下数量及链接可用性是当次结果，可能随平台变化。

| 来源与样例 | 实际结果 | 本次没有验证的部分 |
| --- | --- | --- |
| 7TV 公共集合 `GET https://7tv.io/v3/emote-sets/global` | 45 项；PETPET `01FSNK0GP000038GDBZ91HT6SQ` 的官方 CDN WebP 为 14,578 字节、128×128、5 帧、0.30 秒，含透明像素 | 任意用户频道、集合分页、全部 AVIF 变体及失效素材 |
| BetterTTV 公共集合 `GET https://api.betterttv.net/3/cached/emotes/global` | 65 项；SourPls `566ca38765dbbdab32ec0560` 的官方 CDN `3x` 文件为 3,755,087 字节、140×140、492 帧 GIF、14.76 秒 | 该 GIF 的三种转换、频道与共享表情目录 |
| FrankerFaceZ 公共集合 `GET https://api.frankerfacez.com/v1/set/global` | 23 项；ZrehplaR `9` 的官方 CDN PNG 为 42,534 字节、132×120，实际图片校验通过 | 动画及任意频道集合，不能据静态样例宣称动画覆盖 |
| LINE Store 动态贴图包 `5428032` | 页面含封面与 24 个贴图预览对象；贴图 `110407190` 的公开 `animationUrl` 实际返回 258,586 字节、370×300、17 帧、2 秒 APNG，含透明像素 | 其余 23 张的下载、弹出 / 有声 / 自定义文字贴图及 emoji 商品；不推断未展示或购买后素材 |
| SVGA 官方 `kingset.svga` | 160,200 字节；有界 zlib 解压后按官方 protobuf 字段读取为 750×750、20 fps、56 帧，10 份图像和 17 个精灵 | 完整渲染、遮罩、矢量及音轨还原 |
| B站装扮 `2156` 的 `thumbup.properties.image_ani` | 默认请求返回 412；使用常规浏览器 UA 与 B站 Referer 后详情返回 `code=0`。实际 `.bin` 为 64,008 字节，解压后是 SVGA 2.1.0，360×540、20 fps、50 帧，13 份图像和 13 个精灵 | 尚未渲染或转 GIF；未验证所有装扮。扩展名为 `.bin`，不能只识别 `.svga` 后缀 |
| Signal 官方 Zozo 分享包 | 从官方博文读取完整分享链接，保留 `#` 后包参数；请求 `cdn.signal.org` 与 `cdn2.signal.org` 时发生证书链验证错误 | 清单、认证解密、单贴纸、动画及整包下载均未成功验证；保持 TLS 验证，没有把协议可行当作实测成功 |
| Noto 官方 `1f929` | 官方 CDN `512.webp` 为 580,418 字节、512×512、60 帧、1.8 秒；`lottie.json` 为 46,757 字节，元数据 1024×1024、60 fps、120 帧、2 秒，无外部资源与字体字段 | 未执行该 Lottie 渲染；尺寸超过本站当前 Lottie 512×512 限制，不能直接宣传现有渲染器支持。WebP 与 Lottie 版本时长也不能互相代替 |

其中 7TV WebP 与 LINE APNG 各自通过现有 `animated.prepare()` 和 `tasks.convert()` 生成 GIF、实况 ZIP、动态照片，共 **6 份研究产物**：GIF 分别为 29,591 / 365,419 字节，均多帧且保留透明像素；实况包为 11,754 / 69,219 字节，JPG / MOV 配对标识一致；动态照片为 12,521 / 69,990 字节，XMP 所指附加 MP4 可解码。此处为转换层复用验证，尚未经过新平台的正式 Web 解析 / 签名代理流程，也未验证实体手机导入。

### 实现路径与可宣传范围

1. **先做直播表情统一入口**：单个表情链接优先；集合返回分页目录，让用户选择素材。7TV 本次返回 26 位 ULID，不能只接受历史 24 位十六进制编号。原格式与转换结果分开标注，不把静态图改名为 GIF；保持现有 600 帧 / 30 秒等限制，较长素材先保留原格式下载。
2. **SVGA 单独完成渲染验证**：基于文件头与解压结构识别 1.x ZIP / JSON 和 2.x zlib / protobuf，再校验尺寸、帧率、帧数、图像总量、解压量与外部引用。逐帧处理变换、透明度、裁切、遮罩与矢量，输出透明中间文件后复用现有转换层。不能只提取内嵌 PNG 就称为动画还原。[官方格式定义](https://github.com/svga/SVGA-Format/blob/master/proto/svga.proto)和 [Web Lite 播放器](https://github.com/svga/SVGAPlayer-Web-Lite)可供核对；Lite 明确不支持 1.x 和音频，[旧 Web 播放器](https://github.com/svga/SVGAPlayer-Web)已归档，需分别评估实现与依赖。
3. **LINE 先限制为页面已公开展示的预览**：从真实 `data-preview` 读取地址和类型，不猜购买后内容；封面与贴图分开。APNG 判断依靠真实帧，不依赖 `.png` 后缀。弹出、有声与自定义文字分别补样例后再扩大范围。
4. **Signal 无账号路线保持不变**：完整分享链接中的包密钥用于本地解密，不要求账号凭证；不得丢弃 URL fragment、回显密钥或写入普通访问日志。核对官方 protobuf、HKDF 和先认证再解密的流程，待可信 TLS 路径下真实取得清单及文件后才确定接入范围。
5. **Noto 复用官方素材，不重复建设素材库**：官方页面脚本已给出 `https://fonts.gstatic.com/s/e/notoemoji/latest/{codepoint}/512.webp`、`512.gif` 与 `lottie.json`。保留真实 Unicode 序列和动画格式；官方页面说明动画资源采用 CC BY 4.0，不能以字体仓库其他内容的许可替代。

### SEO 与开源传播假设

| 方向 | 完成后可覆盖的搜索意图 | 更适合展示的开源成果 |
| --- | --- | --- |
| 直播表情 | `7TV emote downloader`、`7TV to GIF`、`BTTV emote download`、`FFZ emote download`、直播表情包下载 | 三站统一结果、可复现样例、透明边缘与原始时序对比；作为英文 README 的可体验能力 |
| SVGA / B站 | `SVGA 转 GIF`、`SVGA 转 MP4`、`B站点赞动画下载`、`B站装扮动画导出` | 有界解码和渲染、遮罩 / 矢量回归样例、与真实播放器逐帧对照；技术复用价值高于再增加一个普通图片域名 |
| LINE | `LINE 動態貼圖下載`、`LINE sticker to GIF`、`APNG 转 GIF 透明` | 手机网页选择、公开预览与原文件身份校验、现有转换能力的完整示例 |
| Signal / Noto | `Signal sticker downloader`、`Signal stickers to PNG`、`animated emoji download`、动态 emoji 转 GIF | Signal 公开包无需账号的可复现验证；Noto 的许可署名、Unicode 定位与格式转换示例 |

这些词是待验证的内容方向，不是已有排名或确定流量。首批只为真实可用的入口发布工具页与教程，完成中英文、相关工具互链和站点图；通过搜索展现 / 点击、解析成功率、下载完成率与 GitHub 引荐观察效果。公开样例、能力边界和可复用实现有助于开源传播，但不能承诺因此增长多少 Star，不设置“先 Star 才下载”。

本轮额外资料：[LINE 真实样例页](https://store.line.me/stickershop/product/5428032/en)、[SVGA 官方样例](https://github.com/svga/SVGA-Samples/blob/master/kingset.svga)、[Signal 清单协议](https://signal.art/addstickers/Stickers.proto)、[Signal 客户端协议说明](https://github.com/signalstickers/signalstickers-client/blob/master/STICKERS_INTERNALS.md)、[Google 官方动画图库](https://googlefonts.github.io/noto-emoji-animation/)。

## 本轮实现与验证

上述候选已接入解析、下载与转换流程；生产站点的实际能力以部署版本和平台响应为准。新增六个工具页、三篇教程和中英文内容，纳入首页互链、站点图及结构化数据。搜索词是内容方向，尚无本轮流量、排名或 Star 增长数据。

| 能力 | 已接入范围 | 边界 |
| --- | --- | --- |
| 7TV / BetterTTV / FrankerFaceZ | 单个表情及有限公开集合，官方 CDN 原格式下载；动态素材复用 GIF / 实况 / 动态照片流程 | 每次最多 200 份素材，不递归下载；FFZ 本轮真实验证为静态 PNG，频道和所有动画变体未全面实测 |
| LINE | Store 商品与公开贴图预览，按页面真实 `data-preview` 读取静态 / 动态地址 | 不猜购买后内容，不合成声音或自定义文字；弹出贴图只在公开预览提供对应地址时展示 |
| Signal | 完整公开分享包，HKDF / HMAC / AES-CBC 认证解密后按需下载 | 官方页面使用的 `cdn-ca.signal.org` 解决标准 CA 校验问题，TLS 校验始终开启。包密钥仅保存在短期内存引用中，响应及素材下载 URL 不含密钥；服务重启 / 引用过期需重新解析，不读取账号或聊天 |
| Noto Animated Emoji | 官方 Unicode 资源链接，WebP 动画和可取得的 GIF / Lottie 原文件 | 保留 Google 出处与 CC BY 4.0 署名；本轮 Lottie 原文件尺寸超过现有 512×512 渲染限制，仅下载，不宣称可转换 |
| SVGA / B站赞动画 | 1.x ZIP / JSON、2.x zlib / protobuf，位图、矢量、变换、透明度、裁切与遮罩；识别 B站动画字段中的 `.bin` | 8 MiB 输入、32 MiB 解压、画布最长边 1024、最多 600 帧 / 30 秒、总输出像素 1 亿；含音轨时拒绝转换，原文件可下载 |
| PAG | 固定官方 libpag 4.5.85 JS / WASM，本地 Chromium 无外部网络逐帧渲染 | 8 MiB、最长边 1024、最多 600 帧 / 30 秒、总像素 1 亿；暂不支持字体图层。依赖及完整许可随包保存，脚本可重现下载并校验摘要 |
| VAP | 读取 MP4 `vapc` 的 RGB / alpha 区域，恢复透明帧 | 32 MiB、输出最长边 1024、最多 600 帧 / 30 秒；须有配置，不猜布局；外部融合素材不支持 |
| Spine | ZIP 内含 JSON、atlas 和图集图片；上传后先选择动画，支持区域附件、普通骨骼变换、附件切换、绘制顺序及颜色时间轴 | 声明版本 3.5–3.8、4.0–4.2；真实渲染覆盖 3.8 / 4.2，4.0 / 4.1 元数据已核对。最多 600 帧 / 20 秒；网格、裁切附件、IK / 路径 / 物理约束、特殊混合或继承明确报错；不包含官方 Spine Runtime |

特殊文件支持公开 `.svga` / `.pag` / `.vap` / `.spine.zip` 链接及本地上传；普通 `.mp4` 上传后识别有配置的 VAP。Spine ZIP 必须自带素材，不从外部路径补取。渲染在独立进程中执行，限制墙钟、CPU、进程树内存及并发；超时和取消回收子进程。所有路径沿用签名、限流、配额与安全下载，特殊文件代理强制附件和非可执行类型，不放宽网站 CSP。

### 真实 Web 烟测

临时本地 Web 服务使用隔离数据目录，通过正式解析、签名代理、准备和转换 API 验证；外部平台无账号凭证。下载了原文件，并检查转换 GIF 为多帧且有可见像素、实况 JPG / MOV 配对标识及动态照片 XMP 指向的附加视频。透明来源的 GIF 保留透明像素，原本不透明的 SourPls 不凭空去背景。

- 7TV PETPET、BTTV SourPls、LINE `5428032` 首张 APNG、Noto `1f929` WebP。
- SVGA 官方 `kingset.svga`、1.x `matteBitmap_1.x.svga`、B站装扮 `2156` 的真实 SVGA `.bin`。
- PAG 官方 `like.pag`、VAP 官方 Android demo、SpineBoy 3.8 / 4.2 的 `walk`。
- Signal 官方 Zozo 静态 WebP 已验证下载；公开 PumpkinCat 包 20 项，首张 512×512、20 帧 APNG 已走完整转换。

上述 12 组动画各完成 GIF、实况包、动态照片，共 36 份真实 Web 转换产物；转换片段最长取 2 秒以控制烟测成本，完整渲染后的 MP4 单独验证下载。未验证全部集合、全部素材、平台未来响应或实体手机导入。批量烟测触发 429 时按 `Retry-After` 等待，没有取消现有限流。

离线用例补充格式像素与透明度、遮罩与 KEEP、认证解密和篡改、引用过期、上传动画选择、签名与文件类型隔离、渲染进程树取消回收；离线替身不计入上述真实平台成功。核心 847 项、独立账号助手 439 项通过，Ruff、ESLint、字体 / CSP / SEO 检查通过。Docker Python 3.12 中禁用外网后完成 SVGA 1.x、PAG、VAP、Spine 真实渲染。

页面使用本地 Chromium / Playwright 检查（当前无 Browser 插件），覆盖 1440px 桌面、390px 与 320px 窄屏、中英文、深色主题、等待 / 成功 / 失败、清空、旧请求隔离、重复提交、重试及超长动画名称。真实交互覆盖 7TV 三种格式切换 / MP4 下载和 Spine 上传选择 / GIF；其余格式的真实转换见 API 烟测。抖音、B站与 X 的真实链接回归成功；小红书旧短链在本轮和修改前 HEAD 均跳到主页并解析失败，未列为成功。实体 iPhone / Android 导入仍未验证。

资料：[Signal 官方网页](https://signal.art/addstickers/)、[SVGA 样例](https://github.com/svga/SVGA-Samples)、[libpag](https://github.com/Tencent/libpag)、[VAP](https://github.com/Tencent/vap)、[Spine JSON 说明](https://esotericsoftware.com/spine-json-format)、[Google 动画 emoji](https://googlefonts.github.io/noto-emoji-animation/)。

## 其他候选，尚未接入 Web 工具

| 方向 | 可以解决的问题 | 接入前必须验证 |
| --- | --- | --- |
| 评论区图片与实况 | 保存评论自身的素材，避免拿到被评论作品的封面 | 评论 ID 的可靠入口、无需账号的公开响应、实况照片与视频配对字段；不能从一张图片猜测配套视频。仓库账号助手已有独立的评论/私信流程，不能直接将其账号接口搬进公开 Web |
| Spotify Canvas | 保存短循环背景并转 GIF / 实况 | 已查到的下载器依赖登录 Cookie；先确认是否存在稳定公开入口。当前不要求访客提交账号 Cookie，也不发布可用功能页 |

资料入口：[Pixiv 对 Ugoira 的说明](https://www.pixiv.help/hc/en-us/articles/235584628-What-are-Ugoira)、[PixivUtil2](https://github.com/Nandaka/PixivUtil2)、[Spotify Canvas Downloader](https://github.com/Delitefully/spotify-canvas-downloader)。

## 搜索入口与验证方式

已上线能力各使用独立工具页，教程解决具体操作问题，例如「Steam 头像框 PNG 为什么不动」「HoYoPlay 启动器壁纸怎么下载」。首页、相关工具和教程互链，沿用站点图、JSON-LD、中英文翻译及自托管字体。

新候选只有完成真实链接、下载和三种格式转换验证后，才加入功能宣传和 SEO 页面。增长效果应从搜索展现、点击、解析成功率及 GitHub 自然流量观察，不把候选平台名称堆成尚不可用的落地页。
