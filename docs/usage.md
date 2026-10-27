# 使用说明与平台支持

[返回 README](../README.md) · [English](usage.en.md)

[功能](#功能) · [支持的平台](#支持的平台) · [音乐封面](#qq-音乐与网易云音乐封面下载) · [其他素材](#steam-动态素材hoyoplay-壁纸与-apple-music-封面)

## 功能

### 提取

- 无水印视频原始码流
- 小红书原图（常见 1920×2560）
- 抖音 H.265 高清档 + 背景音乐
- YouTube、B站可用高清档位服务端合并，按来源实际返回的清晰度选择
- 实况图识别与整篇打包
- QQ 音乐 / 网易云音乐歌曲、专辑与歌单封面提取
- 直链代理，浏览器直接播放不 403
- 失败按「已删除 / 需登录 / 被限流」分类

### 转换

- **GIF** ≤30s，帧率 5–30、宽度 160–960、速度 0.5–3×，两遍调色板编码
- **iPhone 实况** ≤10s，JPG + MOV 配对，`ContentIdentifier` 双向写入并回读校验
- **安卓动态照片** 内嵌 MP4 的 JPG，Motion Photo + MicroVideo XMP
- **原样打包** 平台实况图不重编码，只换封装
- **本地视频** 上传即转，≤300 MB
- **动态音乐封面** 按真实图片格式解码，转 GIF、实况照片或安卓动态照片

### 网站

- 编辑风极简界面，手机端完整适配
- 字体全部自托管，页面零外部请求
- 工具页与操作教程，JSON-LD 结构化数据
- 公网加固：限流、配额、签名、SSRF、CSP
- Docker 非 root 运行
- yt-dlp 自动升级跟进平台改版
- 站长统计页 `/stats`：每小时 / 每天多少人在用、各平台成功率、失败原因

## 支持的平台

| 平台 | 视频 | 图集 | 实况图 | 清晰度 | 说明 |
|---|:-:|:-:|:-:|---|---|
| 抖音 | ✅ | ✅ | ✅ | 720p – 1080p，H.265 | 背景音乐可单独下载 |
| 小红书 | ✅ | ✅ | ✅ | 最高档 | 链接需带有效 `xsec_token`，现复制现用 |
| 快手 | ✅ | ✅ | – | 平台默认 | |
| YouTube | ✅ | – | – | 可读直链，按实际返回的高清档位合并下载 | Docker 内置原生会话 / SABR 失败兜底 |
| X (Twitter) | ✅ | ✅ | – | 最高码率 | 敏感推文走 fxtwitter 兜底 |
| B站 | ✅ | ✅ | ✅ | 480p 直链，720p / 1080p 服务端合并 | b23.tv 短链可用；动态里的图和实况也能取；番剧、电影不支持 |
| Instagram | ✅ | ✅ | – | 平台默认 | 多图帖取原图，图片和视频混着的也行 |
| QQ 音乐 | – | 封面 | – | 专辑优先验证源文件，歌单 1000px | 没有源文件时提供验证可用的高清版本；只提取封面 |
| 网易云音乐 | – | 封面 | – | 平台提供的原始尺寸 | 歌曲、专辑、歌单分享链接；动态封面可保留原格式或转 GIF / 实况 / 动态照片 |
| Steam 点数商店 | ✅ | GIF / APNG 等素材 | – | 平台提供的原始素材 | 单个物品链接；动态头像、透明头像框、贴纸、MP4 / WebM 背景；动画可转换 |
| HoYoPlay | ✅ | 当前启动器壁纸 | – | 平台提供的原始素材 | 选择四款游戏，读取当前国服公开背景；有视频时可转换，不含历史档案 |
| 鸣潮 | 当前背景视频 | 启动器首帧或静态图 | – | 国际服当前公开文件 | [工具](https://ynvan.com/wuthering-waves-wallpaper)；不含历史版本；视频可转三种格式 |
| 米游社 | – | 正文原图与 GIF | – | 平台提供的原始文件 | [工具](https://ynvan.com/miyoushe-images)；单篇帖子，不含主页、评论与 HoYoLAB；GIF 可转换 |
| B站装扮与收藏集 | 公开动态素材 | 公开图片 | – | 平台当前返回的素材 | [工具](https://ynvan.com/bilibili-assets)；每份独立下载与转换，不解锁账号权益；支持公开动画字段中的 SVGA；其他专用格式按各自边界处理 |
| 7TV / BetterTTV / FrankerFaceZ | – | 公开直播表情 | – | 官方 CDN 文件 | [工具](https://ynvan.com/stream-emotes)；单个表情与有限公开集合，动态素材可转换 |
| LINE Store | – | 公开贴图预览 | – | PNG / APNG 原格式 | [工具](https://ynvan.com/line-stickers)；只读取实际公开预览，不包含购买后内容、声音或自定义文字合成 |
| Signal | – | 公开分享包 | – | 实际 WebP / PNG / GIF | [工具](https://ynvan.com/signal-stickers)；需完整公开分享链接，无账号凭证，认证解密和短期素材引用 |
| Noto Animated Emoji | – | 官方动画 emoji | – | WebP / GIF / Lottie | [工具](https://ynvan.com/animated-emoji)；WebP 可转换，保留 Google 与 CC BY 4.0 署名；超过现有限制的 Lottie 仅下载 |
| SVGA / PAG / VAP / Spine | 渲染 MP4 | 透明帧 | – | 有界渲染 | [SVGA](https://ynvan.com/svga-to-gif)、[其他格式](https://ynvan.com/animation-converter)；公开文件链接或上传；Spine 仅区域附件，复杂约束 / 网格明确拒绝 |
| Telegram 贴纸 | – | TGS / WebP | – | 公开目录已收录文件 | [工具](https://ynvan.com/telegram-stickers)；无需账号凭证；TGS 可转透明 GIF / 实况 / 动态照片；不含私人素材或全部实时贴纸包 |
| 微信视频号 | 公开视频 | 公开预览封面 | – | 以实际文件为准，不信任接口画质标签 | [工具](https://ynvan.com/wechat-channels)；官方 sph 分享链接，官方预览核对作品，BugPk 公益接口补充视频；匿名下载与转换，不要求账号凭证，公益接口失败时保留封面 |
| Discord | – | 公开目录贴纸 | – | APNG / Lottie JSON 等目录实际格式 | [工具](https://ynvan.com/discord-stickers)；公开贴纸包商店或编号链接、对应的 stickers 素材链接；逐项下载，动态素材可转透明 GIF / 实况 / 动态照片；不读取私人服务器或聊天 |
| Apple Music | 动态封面 | 封面 | – | 图片请求来源标注尺寸；动态视频选最高可用档 | 歌曲、专辑、歌单公开封面；动态视频按作品提供情况显示，不下载歌曲音频 |
| Pixiv | Ugoira 合成 MP4 | 插画 / 漫画原图、帧包 | – | 平台提供的图片与播放帧包 | [原图与小说](https://ynvan.com/pixiv-download)、[动图](https://ynvan.com/pixiv-ugoira)；小说 TXT、封面与插图分开保存；作者、系列、标签、榜单、Collections 分页点选作品。转换 ≤600 帧 / 30 秒，另有体积与像素限制；不接账号凭证 |
| Pixiv Sketch | – | 公开帖子原图 | – | 平台返回的 original 图片 | [工具](https://ynvan.com/pixiv-sketch)；帖子链接或作者目录，按页选择；不包含直播、私密和年龄受限内容 |
| 微博、西瓜、皮皮虾、AcFun、TikTok … | ✅ | 部分 | – | | 30+ 平台，其余交给 yt-dlp 兜底 |

> 解析依赖各平台网页结构，平台改版可能暂时失效。上游 parse-video-py 更新较勤，修了某个平台时可以对照着改 `parser/` 里的解析器（不能直接覆盖，见[贡献](development.md#贡献)）。

## QQ 音乐与网易云音乐封面下载

复制歌曲、专辑或歌单的分享链接，即可在线提取对应封面。**歌单取歌单本身的封面，不是第一首歌曲的专辑图。** 不用安装插件，也不用在本站登录。

| 需求 | 在线入口与结果 |
|---|---|
| QQ 音乐歌曲 / 专辑 / 歌单封面 | [QQ 音乐封面提取](https://ynvan.com/qq-music-cover)：优先验证专辑源文件，实测样本为 2048px；无源文件时提供可用高清版本，歌单目前请求 1000px |
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

## 界面预览

<table>
  <tr>
    <td width="50%"><img src="screenshot-convert.png" alt="转换面板：拖缩略图条选段，生成实况照片"></td>
    <td width="50%"><img src="screenshot-guide.png" alt="教程页"></td>
  </tr>
  <tr>
    <td align="center"><sub>任意视频截一段做成实况照片，封面帧自己挑</sub></td>
    <td align="center"><sub>教程页：每篇对准一个具体问题</sub></td>
  </tr>
</table>

## 特殊动画文件

上传 SVGA、PAG、VAP，或包含 JSON、atlas 和全部图集图片的 Spine ZIP；Spine 上传后先选择动画。渲染后可下载 MP4，也可转透明 GIF、实况照片或动态照片。GIF 保留透明背景，照片使用普通视频画面。普通 MP4 上传也会识别 VAP 配置。

自托管需安装 `python -m pip install -e ".[web,cli,animations]"` 并运行 `playwright install --with-deps chromium`；Web 镜像已包含依赖。SVGA / PAG 输入限 8 MiB，VAP / Spine 限 32 MiB，另有帧数、时长、像素、内存与超时限制。SVGA 音轨、PAG 字体、VAP 外部融合素材和 Spine 复杂类型会明确拒绝，详见[实现与实测边界](media-assets.md#本轮实现与验证)。
