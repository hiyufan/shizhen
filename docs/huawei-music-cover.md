# 华为音乐动态封面

首页粘贴华为音乐歌曲、专辑或歌单分享链接即可解析，无需登录。支持 `url.cloud.huawei.com` 短链，以及下表中的官方音乐分享页。分享链接会按作品编号转为统一的音乐页面，下载与转换使用同一作品。其他华为云分享文件不在范围内。

## 分享入口

| 部分 | 接受的入口 |
| --- | --- |
| HTTPS 分享域名 | `portal-drcn.music.dbankcloud.cn`、`portal-drcn.music.cloud.huawei.com`、`api-drcn.music.hicloud.com`、`portal-drcn.music.hicloud.com` |
| 页面路径 | `/music-apph5-service/h5/index.html`、`/music-apph5-service/musich5/index.html`，以及省略 `index.html`、保留末尾斜杠的两个目录入口 |
| 歌曲路由 | `#/musicshare/1?id=作品编号`；官方默认歌曲路由 `#/musicshare?id=作品编号` 也可使用 |
| 专辑与歌单 | 专辑 `/musicshare/2`，歌单 `/musicshare/4` 或 `/musicshare/5`；歌单兼容 `listid` 参数与分页 |

2026-11-15 实际读取四个域名与四种路径的 16 个页面，均返回华为音乐官方页面及相应脚本；旧域名的[专辑分享页](https://api-drcn.music.hicloud.com/music-apph5-service/h5/index.html#/musicshare/2?id=5345008&shareChannel=qq)和[另一版歌曲分享页](https://portal-drcn.music.hicloud.com/music-apph5-service/musich5/#/musicshare?id=1036947)与现有入口加载相应官方脚本。[官方路由脚本](https://h5hosting-drcn.dbankcdn.cn/cch5/HW-Music/online/static/js/app.js?v=1793065751012)将 `/musicshare` 重定向到 `/musicshare/1`；`h5` 与 `musich5` 的脚本具有相同的分享路由。没有末尾斜杠的 `/h5`、`/musich5` 实测返回 403，`api-drcn.music.dbankcloud.cn` 的分享页实测返回 404，不作为可用入口加入。其他地区域名没有可验证的公开分享样例，暂不扩大支持范围。

真实解析核对了两种旧域名的《小情歌》默认歌曲入口、《大鱼》专辑、目录入口下的“让封面动起来”歌单，以及旧公开样例《听·电影》专辑。前三种返回对应动态封面或歌曲目录，《听·电影》只返回静态封面；不会因为域名兼容就把所有作品标为有动画。

## 封面与目录

- 歌曲：读取对应作品 `picture.dynamicImgURL` 的原始动态 WebP，并单独保留静态封面。
- 专辑：优先取专辑本身的动态封面；如果只在曲目上提供，检查接口首批 20 首，且必须属于同一专辑、静态封面一致。不会借用另一首推荐歌曲的动画。
- 歌单：按页展示歌曲目录，每页 20 首，选曲后提取其封面。不会把歌单当作一条视频或自动下载整份歌单。
- 没有公开动态字段时，明确显示仅提供静态封面；不从歌曲音频、MV、空间音频文件或播放器视觉特效猜测动态素材。

“下载动态 WebP”保留来源文件的字节、尺寸与帧时序。“下载 MP4”由原始 WebP 逐帧生成无声 H.264 视频，保留实际尺寸与帧时序（奇数边长向下取偶数），不添加边框、不放大或插帧；MP4 是转换产物，并非平台提供的视频原文件。可以继续转 GIF、iPhone 实况照片或安卓动态照片；GIF 从保留透明度和逐帧时长的中间文件生成，不经预览 MP4 二次压缩。转换仍受帧数、时长、像素、任务并发与存储配额限制。仅针对华为明确的动态封面地址，将累计解码像素上限从通用的 1 亿放到 2 亿，以容纳实测的 1000×1000、144–160 帧素材；其他图片保持原有上限。

## 公开样例与验证范围

公开入口来自华为官方分享页及其当前加载的 JavaScript，不使用账号 Cookie、Token 或应用私有接口凭证：

- [《大鱼》歌曲分享页](https://portal-drcn.music.dbankcloud.cn/music-apph5-service/h5/index.html#/musicshare/1?id=2168177)：1000×1000，160 帧，每帧 60 ms，共 9.6 秒；原文件 16,733,076 字节。
- [《夜曲》歌曲分享页](https://portal-drcn.music.dbankcloud.cn/music-apph5-service/h5/index.html#/musicshare/1?id=1066775)：1000×1000，144 帧，每帧 60 ms，共 8.64 秒；原文件 5,445,574 字节。
- [《乌鸦》歌曲分享页](https://portal-drcn.music.dbankcloud.cn/music-apph5-service/h5/index.html#/musicshare/1?id=M_b-4EQuyWFISLIOX)：不透明作品编号，1000×1000，144 帧，共 8.64 秒；原文件 9,130,802 字节。
- [《小情歌》另一版歌曲分享页](https://portal-drcn.music.cloud.huawei.com/music-apph5-service/musich5/index.html#/musicshare/1?id=1036947&listid=&ishm=true&shareChannel=1)：2026-11-15 验证两个分享域名与 `h5` / `musich5` 路径的兼容解析；原始动态 WebP 为 1000×1000、144 帧、8.64 秒、6,904,208 字节，完整 MP4 的尺寸、帧数与时长一致，并完整解码。另截取 1 秒完成 GIF、实况包和动态照片烟测，核对实况配对标识及动态照片附加视频；未做手机相册实机验收。
- [《大鱼》专辑分享页](https://portal-drcn.music.dbankcloud.cn/music-apph5-service/h5/index.html#/musicshare/2?id=168016)：专辑图片没有动态字段，但同封面的所属曲目提供上述 WebP。
- [“让封面动起来”歌单](https://portal-drcn.music.dbankcloud.cn/music-apph5-service/h5/index.html#/musicshare/4?id=OqsyPlZ9qWO0pHDHE)：查询时有 91 首，首批 20 首均返回动态封面地址；目录数量不是已经下载的文件数量。
- [《永不完结的歌》专辑短链](https://url.cloud.huawei.com/sL5lwXz8qY)：已验证短链跳转与静态封面；该样本没有公开动态封面。

三个动态样本均完整下载、逐帧读取，验证准备后的视频尺寸、帧数、起始时间和总时长，并分别截取 1 秒生成 GIF、实况包与动态照片。直接下载 MP4 也已核对三个完整样本的尺寸、帧数、零起始时间与完整时长，并完整解码；离线变帧率样本另核对各帧时间戳和末帧停留时间。实况包核对 JPEG / MOV 配对标识，动态照片核对附加视频；这不等于手机相册已实机验收。下载原文件不会提升平台素材本身的清晰度。其他地区分享域名、所有歌单分页与仅在客户端呈现的动态效果尚不作支持承诺。

离线测试覆盖四个分享域名与四种页面路径的组合，并单独检查歌曲、专辑和两类歌单路由。测试也覆盖默认歌曲路由保留作品编号、短链跳转到旧域名和目录入口、MP4 下载重新请求同一作品、域名和路径校验、短链越站与循环、数字和不透明编号、静态回退、错误作品、专辑封面归属、目录分页，以及图片转换预算。真实平台烟测与离线替身测试分别记录。
