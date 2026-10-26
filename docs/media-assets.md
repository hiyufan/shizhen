# 公开动态素材：实现范围与后续研究

2026-10-26。优先围绕「取出动画 → 下载原格式 → GIF / 实况 / 动态照片」提供完整流程。下面是实现验证与候选方向，不代表搜索量、独占功能或 Star 增长预测。

## 已接入

| 来源 | 入口和边界 | 验证重点 |
| --- | --- | --- |
| Steam 点数商店 | `/steam-assets`；单个 `/reward/` 物品，公开头像、头像框、贴纸和背景素材 | 精确查询 definitionids 并核对物品与 appid；动画可能在 small 字段，large 可能只是静态预览；下载保留 GIF / APNG / MP4 / WebM |
| HoYoPlay | `/hoyoplay-wallpaper`；选择原神、星穹铁道、绝区零或崩坏 3，读取当前国服启动器公开背景 | 按 game_id 与 biz 匹配游戏；图片去重，视频按实际返回展示；不提供历史版本档案 |
| Apple Music | `/apple-music-cover`；歌曲、专辑、歌单的公开网页封面 | 只读取作品 header，不能回退到列表中的其他歌曲；静态图与公开动态视频分别下载，不请求歌曲音频 |

Apple Music 动态封面经安全 HTTP 客户端读取 HLS，校验 Apple 媒体主机、有限播放列表、时长、分段数量、字节范围与总下载量；拒绝加密媒体。先在本地拼接视频，再让 FFmpeg 读取本地文件，不把远程播放列表交给 FFmpeg。

Steam 透明动画另存转换用的透明中间文件，GIF 从该文件生成；预览与实况使用 H.264。APNG 的默认静态预览不计入动画帧，帧时长按毫秒保留。缓存登记与清理同时覆盖透明中间文件。

资料入口：

- [Steam 官方点数商店素材规范](https://partner.steamgames.com/doc/marketing/pointsshopitems?l=schinese)：头像、头像框、贴纸与背景的实际格式。
- [HoYoPlay Launcher Background](https://github.com/UIGF-org/HoYoPlay-Launcher-Background)：已有启动器背景提取项目，说明不是无人涉及的方向。本项目使用独立解析与现有安全下载封装。
- [Apple Music Artwork Search](https://github.com/NopXx/apple-music-artwork-search)：已有封面工具；本项目侧重与 GIF、实况转换的衔接。

## 下一批候选，尚未接入 Web 工具

| 方向 | 可以解决的问题 | 接入前必须验证 |
| --- | --- | --- |
| Pixiv Ugoira | 保留逐帧时长，导出 GIF / 视频 / 实况 | 公开作品的匿名元数据可用性、帧 ZIP 与 delay 数据是否完整、登录限制；ZIP 路径与解压体积上限。已有 PixivUtil2 等成熟工具，差异应放在浏览器内的完整转换流程 |
| 评论区图片与实况 | 保存评论自身的素材，避免拿到被评论作品的封面 | 评论 ID 的可靠入口、无需账号的公开响应、实况照片与视频配对字段；不能从一张图片猜测配套视频。仓库账号助手已有独立的评论/私信流程，不能直接将其账号接口搬进公开 Web |
| Spotify Canvas | 保存短循环背景并转 GIF / 实况 | 已查到的下载器依赖登录 Cookie；先确认是否存在稳定公开入口。当前不要求访客提交账号 Cookie，也不发布可用功能页 |

资料入口：[Pixiv 对 Ugoira 的说明](https://www.pixiv.help/hc/en-us/articles/235584628-What-are-Ugoira)、[PixivUtil2](https://github.com/Nandaka/PixivUtil2)、[Spotify Canvas Downloader](https://github.com/Delitefully/spotify-canvas-downloader)。

## 搜索入口与验证方式

已上线能力各使用独立工具页，教程解决具体操作问题，例如「Steam 头像框 PNG 为什么不动」「HoYoPlay 启动器壁纸怎么下载」。首页、相关工具和教程互链，沿用站点图、JSON-LD、中英文翻译及自托管字体。

新候选只有完成真实链接、下载和三种格式转换验证后，才加入功能宣传和 SEO 页面。增长效果应从搜索展现、点击、解析成功率及 GitHub 自然流量观察，不把候选平台名称堆成尚不可用的落地页。
