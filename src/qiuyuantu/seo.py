"""SEO 用的落地页定义：同一个工具，按搜索意图给每类需求一个独立 URL、标题和文案。

首页拿"视频图片提取 / GIF / 实况"这些泛词，落地页各拿一个精确词（"抖音去水印"、"视频转GIF"…），
互相内链，并和 guides.py 里的教程互链。文案都是给人看的，不堆关键词。

标题控制在 30 个汉字以内（百度截断位置），描述 80 个汉字以内。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .guides import GUIDES, Guide

SITE_NAME = "求原图"
SITE_URL = os.environ.get("PARSE_VIDEO_SITE_URL", "").rstrip("/")  # 如 https://example.com；不填就按请求地址
UPDATED = "2026-10-27"

# 站长验证 / 统计代码：直接贴 <meta> 或 <script> 片段
SITE_VERIFICATION_HTML = os.environ.get("PARSE_VIDEO_SITE_VERIFICATION", "")
ANALYTICS_HTML = os.environ.get("PARSE_VIDEO_ANALYTICS", "")

# IndexNow（Bing / Yandex / Seznam / Naver）的提交凭据。这是公开的验证串不是
# 机密：web/pages.py 提供 /{key}.txt 路由，scripts/push_urls.py 用它推送新页面。
INDEXNOW_KEY = "f26b149a71c74f67b3a5d9793914505a"


@dataclass
class Page:
    slug: str  # "" 表示首页
    title: str  # <title>，关键词在前、品牌在后
    description: str
    h1: str  # 允许 <em>（斜体强调的拉丁词）和 <br>
    lead: str
    keywords: str
    placeholder: str = "粘贴分享链接…"
    nav_label: str = ""  # 页脚互链里显示的名字
    faq: list[tuple[str, str]] = field(default_factory=list)  # 这一页专属的问答，排在通用问答前面
    body: list[tuple[str, str]] = field(default_factory=list)  # 这一页独有的正文 (h2, html)，放在工具下方
    guides: list[str] = field(default_factory=list)  # 相关教程 slug
    # 这一页要带上的通用问答（COMMON_FAQ 的键）。整套 7 条只在首页出；落地页各挑最相关的 3 条，
    # 否则 600 多字的通用问答在 9 个页面上一字不差地重复，把各页独有内容稀释成一成左右。
    common: list[str] = field(default_factory=lambda: list(DEFAULT_COMMON))

    @property
    def path(self) -> str:
        return "/" + self.slug if self.slug else "/"

    @property
    def all_faq(self) -> list[tuple[str, str]]:
        return self.faq + [COMMON_FAQ[k] for k in self.common if k in COMMON_FAQ]


COMMON_FAQ: dict[str, tuple[str, str]] = {
    "links": (
        "支持哪些链接？",
        "抖音、小红书、快手、微博、B站、西瓜、皮皮虾、AcFun，以及 YouTube、X（Twitter）、TikTok、Instagram 等平台，还支持 QQ 音乐、网易云音乐的歌曲、专辑和歌单封面。直接粘贴 App 里「复制链接」得到的整段文字即可。",
    ),
    "watermark": (
        "视频有水印吗？图片是原图吗？",
        "视频取的是平台无水印的播放地址；小红书图片会换成原图分辨率，抖音图集取的是不带水印的 JPEG 原始尺寸。个别平台只提供 720p 的无水印版本。",
    ),
    "iphone": (
        "实况照片怎么导入 iPhone？",
        'iOS 只让 App 往相册写实况，网页做不到。iPhone 上解析后点"存实况视频到相册"，再用剪映或视频转实况的 App（比如免费的"一键实况"）转一下；有 Mac 的话，把实况包里的 JPG 和 MOV 一起拖进 Mac 的"照片"App 就是实况，开了 iCloud 照片会同步到 iPhone。',
    ),
    "android": (
        "安卓动态照片怎么用？",
        "下载的 MVIMG_xxx.jpg 直接保存到手机相册，Google 相册、三星、小米、OPPO、vivo 等相册会识别为动态照片，长按即可播放。",
    ),
    "gifsize": (
        "GIF 太大怎么办？",
        "GIF 的体积主要由宽度 × 帧率 × 时长决定。480 像素宽、12 帧、10 秒大约 10 MB；改成 360 像素宽、10 帧、5 秒就只有 2–3 MB。",
    ),
    "ratelimit": (
        '提示"请求太频繁"？',
        "为了让服务对所有人可用，每个 IP 每分钟的解析次数和同时进行的转换任务数有限制。等一会儿再试就好。",
    ),
    "free": (
        "需要注册或付费吗？",
        "不需要。所有功能免费，不用登录，也不会保存你粘贴的链接和下载的文件（临时文件一小时内自动清理）。",
    ),
}

DEFAULT_COMMON = ["watermark", "ratelimit", "free"]
ALL_COMMON = list(COMMON_FAQ)

PUBLIC_ASSET_EXAMPLES = {
    "stream-emotes": [
        ("7TV PETPET", "https://7tv.app/emotes/01FSNK0GP000038GDBZ91HT6SQ"),
        ("BTTV SourPls", "https://betterttv.com/emotes/566ca38765dbbdab32ec0560"),
        ("FFZ ZrehplaR", "https://www.frankerfacez.com/emoticon/9"),
    ],
    "line-stickers": [("LINE", "https://store.line.me/stickershop/product/5428032/en")],
    "animated-emoji": [("Noto Emoji", "https://fonts.gstatic.com/s/e/notoemoji/latest/1f929/512.webp")],
    "svga-to-gif": [("SVGA", "https://raw.githubusercontent.com/svga/SVGA-Samples/master/kingset.svga")],
    "animation-converter": [("PAG", "https://pag.io/file/like.pag")],
}

PAGES: list[Page] = [
    Page(
        slug="stream-emotes",
        title="7TV、BTTV、FFZ表情下载_透明GIF转换 - 求原图",
        description="粘贴 7TV、BetterTTV 或 FrankerFaceZ 公开表情链接，保存原格式，动态表情可转透明 GIF、iPhone 实况包和安卓动态照片。",
        h1="直播表情，<br>保存后接着动。",
        lead="7TV、BTTV、FFZ 的公开表情放在一个入口。粘贴单个表情链接，保留原文件，或把动态素材转成透明 GIF。",
        keywords="7TV emote downloader,7TV to GIF,BTTV表情下载,FFZ表情下载,直播表情包下载",
        placeholder="粘贴 7tv.app、betterttv.com 或 frankerfacez.com 表情链接…",
        nav_label="直播表情下载",
        body=[
            (
                "三站公开表情",
                "7TV 保存 WebP；BTTV 按真实类型保存 GIF 或静态图；FFZ 读取公开图片。支持有限的公开集合，超过 200 项时请改用单个表情链接。",
            ),
            (
                "透明动画转 GIF",
                "动态素材先按真实帧读取，再转换。GIF 保留透明背景；实况与动态照片使用普通视频画面。静态表情直接下载，不会补出动画。",
            ),
        ],
        faq=[
            (
                "需要 Twitch 账号吗？",
                "不需要。这里只读取三个表情平台的公开素材，不访问 Twitch 账号接口。部分 BTTV 链接可能只能从公开全局目录中匹配。",
            ),
            (
                "为什么 WebP 在相册里不动？",
                "一些相册只显示预览帧。选择动态表情的「转 GIF」，再下载转换结果；原始 WebP 也会保留。",
            ),
        ],
        guides=["stream-emotes-to-gif"],
    ),
    Page(
        slug="line-stickers",
        title="LINE动态贴图下载_APNG转透明GIF与实况 - 求原图",
        description="读取 LINE 商店公开贴图预览，保存 PNG、APNG，动态贴图可转透明 GIF、实况包和安卓动态照片。无需登录。",
        h1="LINE 动态贴图，<br>换个地方接着动。",
        lead="粘贴 LINE 商店贴图包链接，选择公开展示的贴图。保留 PNG／APNG 原文件，也能把动态预览转成 GIF。",
        keywords="LINE動態貼圖下載,LINE sticker downloader,LINE sticker to GIF,APNG转GIF",
        placeholder="粘贴 store.line.me/stickershop/product/… 贴图包链接…",
        nav_label="LINE 贴图",
        body=[
            (
                "只读取商店公开预览",
                "封面与正文贴图分开，只列出商店页面已经展示的素材。不包含购买后专属内容、声音或自定义文字；emoji 商品另属范围。",
            ),
            (
                "APNG 也是会动的 PNG",
                "文件后缀虽然是 .png，内部可能有多帧。原文件保留透明边缘；转换成 GIF 后可在更多应用中播放，但 GIF 只有 256 色。",
            ),
        ],
        faq=[
            ("整个包都能下载吗？", "当前逐张选择和下载页面公开预览，不提供购买权益，也不保证覆盖特殊贴图类型。"),
            (
                "弹出贴图和有声贴图怎么办？",
                "只处理页面提供的图片或动画预览，不合成弹出效果和声音。没有可用预览时会提示无法读取。",
            ),
        ],
        guides=["line-signal-stickers"],
    ),
    Page(
        slug="signal-stickers",
        title="Signal贴纸包下载_公开链接保存原图与动图 - 求原图",
        description="粘贴完整 Signal 公开贴纸包分享链接，选择贴纸并保存图片。动态贴纸可转换，无需 Signal 账号或 Bot。",
        h1="Signal 贴纸，<br>从分享链接保存。",
        lead="使用包含完整包参数的 signal.art 分享链接，读取公开贴纸清单。选择图片下载；动态贴纸可以继续转换。",
        keywords="Signal sticker downloader,Signal贴纸下载,Signal stickers to PNG,Signal贴纸转GIF",
        placeholder="粘贴完整的 https://signal.art/addstickers/#… 分享链接…",
        nav_label="Signal 贴纸",
        body=[
            (
                "保留完整分享链接",
                "请保留 # 后面的全部包参数。这些参数属于分享链接，缺失后无法读取素材；不用提供账号密码。",
            ),
            (
                "静态与动态分别处理",
                "清单不会保证每张都有动画。静态贴纸直接下载；转换时会检查实际帧数，静态图片不会被当成动态素材。",
            ),
        ],
        faq=[
            (
                "链接完整还是读不到？",
                "文件可能已经失效，或当前网络无法连接平台。可以重新复制公开分享链接再试；仅有包编号不够。",
            ),
            (
                "临时素材过期怎么办？",
                "重新粘贴原始完整分享链接解析即可。下载与转换链接会过期，不适合作为永久图床地址。",
            ),
        ],
        guides=["line-signal-stickers"],
    ),
    Page(
        slug="animated-emoji",
        title="Noto动态Emoji下载_WebP与GIF转换 - 求原图",
        description="保存 Google Noto 官方动态 emoji 的 WebP、GIF 和 Lottie 原文件，WebP 动画可转透明 GIF、实况包与动态照片。保留 CC BY 4.0 署名。",
        h1="动态 Emoji，<br>找到喜欢的那个。",
        lead="从 Google Noto 官方动画图库复制素材链接，保留官方 WebP、GIF 或 Lottie 文件，再按需要转换。",
        keywords="animated emoji download,Noto emoji GIF,动态emoji下载,透明emoji动画",
        placeholder="粘贴 fonts.gstatic.com 的 Noto 动态 emoji 素材链接…",
        nav_label="动态 Emoji",
        body=[
            (
                "官方素材与署名",
                "素材来自 Google Noto Animated Emoji，采用 CC BY 4.0。下载和转换后仍需保留作者、许可与来源说明。",
            ),
            (
                "原文件与转换结果",
                "WebP 和 GIF 可以继续转换。Lottie 作为原文件下载，不承诺所有尺寸都能渲染；不同格式的帧数和时长可能不同。",
            ),
        ],
        faq=[
            (
                "可以直接输入一个 Emoji 吗？",
                "当前使用官方素材链接，先在官方动画图库选择 emoji，再复制 WebP、GIF 或 Lottie 地址。",
            )
        ],
        guides=["stream-emotes-to-gif"],
    ),
    Page(
        slug="svga-to-gif",
        title="SVGA转GIF与MP4_B站装扮动画下载与转换 - 求原图",
        description="上传 SVGA 或粘贴公开文件、B站装扮链接，逐帧还原动画，转透明 GIF、MP4、实况包与动态照片。支持有界的无声 SVGA 1.x 和 2.x。",
        h1="SVGA 动画，<br>转成能保存的格式。",
        lead="上传 SVGA 文件，或粘贴公开素材、B站装扮详情链接。还原动画后预览，保存透明 GIF、MP4 或实况照片。",
        keywords="SVGA转GIF,SVGA转MP4,B站点赞动画下载,B站装扮动画导出,SVGA converter",
        placeholder="粘贴公开 .svga 文件或 B站装扮详情链接…",
        nav_label="SVGA 转 GIF",
        body=[
            (
                "还原动画，不只取图片",
                "读取逐帧变换、透明度、裁切、遮罩和矢量形状，再生成动画。原文件下载与转换结果分开；不把包里的某张图片冒充完整动画。",
            ),
            (
                "转换范围",
                "无声动画支持 SVGA 1.x ZIP 与 2.x 二进制。文件不超过 8 MiB，画布不超过 1024×1024、600 帧或 30 秒，另有总像素限制；含音轨的文件保留原文件下载。",
            ),
        ],
        faq=[
            (
                "B站的 .bin 是什么？",
                "部分装扮的公开动画使用 .bin 后缀保存 SVGA。使用装扮详情链接可识别对应素材，转换时仍会检查文件内容。",
            ),
            (
                "GIF 和实况都透明吗？",
                "GIF 保留透明背景；MP4、实况和安卓动态照片使用普通视频画面。转换失败时仍可下载平台公开的原始文件。",
            ),
        ],
        guides=["special-animation-to-gif"],
    ),
    Page(
        slug="animation-converter",
        title="PAG、VAP、Spine动画转GIF与视频 - 求原图",
        description="上传 PAG、VAP 或 Spine JSON图集ZIP，预览并转换为透明 GIF、MP4、实况与动态照片。Spine 可选择动画，复杂类型明确提示。",
        h1="特殊动画，<br>预览后再保存。",
        lead="上传 PAG、包含透明区域配置的 VAP，或带骨骼 JSON、atlas 和图片的 Spine ZIP。选择动画后，复用 GIF 与实况转换。",
        keywords="PAG转GIF,VAP转GIF,Spine转GIF,Spine动画导出,PAG converter,VAP animation export",
        placeholder="粘贴公开 .pag、.vap 或 .spine.zip 文件链接，或选择本地文件…",
        nav_label="特殊动画转换",
        body=[
            (
                "PAG 与 VAP",
                "PAG 支持不含字体图层的有界动画。VAP 从文件内配置还原颜色与透明区域；需要额外图片或文字融合的文件不支持，不会猜测缺失配置。",
            ),
            (
                "Spine 先选动画",
                "ZIP 内放一份骨骼 JSON、一份 atlas 和对应 PNG／JPEG。支持 3.5–3.8、4.0–4.2 的区域附件与普通骨骼动画；网格、IK、路径、物理约束和特殊混合模式暂不支持。",
            ),
        ],
        faq=[
            (
                "为什么只有 .skel 不能转换？",
                "这个入口读取 JSON 骨骼与图集，不解析二进制 .skel。请从你有权使用的项目导出 JSON，并把对应图集与图片一起打包。",
            ),
            (
                "文件过大或类型不支持怎么办？",
                "可以缩短动画、降低画布尺寸，或在原制作工具中导出通用视频。工具会说明不支持的类型，不会忽略关键图层后显示成功。",
            ),
        ],
        guides=["special-animation-to-gif"],
    ),
    Page(
        slug="pixiv-download",
        title="Pixiv原图下载_漫画、小说与动图在线解析 - 求原图",
        description="解析 Pixiv 公开插画、漫画、小说和 Ugoira。保存原图、TXT 与帧包，作者、系列、标签、榜单和集合按页选择作品，无需账号。",
        h1="Pixiv 的作品，<br>原图与动画一起保存。",
        lead="粘贴公开作品或目录链接。图片按原格式下载，小说另存 TXT；作者、系列和集合先选作品，Ugoira 还能转 GIF 与实况。",
        keywords="Pixiv原图下载,Pixiv漫画下载,Pixiv小说TXT,Pixiv链接解析",
        placeholder="粘贴 Pixiv 作品、作者、系列或集合链接…",
        nav_label="Pixiv 原图与小说",
        body=[
            (
                "单篇作品与目录",
                "插画和漫画保留正文页序；作者、系列、标签、排行榜与 Collections 按页列出作品，点选后继续解析。目录不会自动整批下载。",
            ),
            (
                "小说与插图分别保存",
                "小说提供纯文本 TXT，保留 Pixiv 原文标记；封面、上传插图和引用作品分别列出。集合里的外部链接只提供原页面入口。",
            ),
            (
                "公开访问的范围",
                "只处理平台匿名提供的内容。私密、年龄受限、已删除、收藏夹和关注动态可能无法读取；URL 限定作品需要完整链接。",
            ),
        ],
        faq=[
            (
                "支持所有 Pixiv 链接吗？",
                "支持主站主要公开作品和目录，以及常见短链、旧作品链接；不保证账号或权限受限内容，也不包含 FANBOX、BOOTH 等独立服务。",
            )
        ],
        guides=["pixiv-original-images", "pixiv-ugoira-to-gif"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="pixiv-ugoira",
        title="Pixiv动图下载_Ugoira转GIF与实况照片 - 求原图",
        description="下载 Pixiv Ugoira 帧包与逐帧时序 JSON，转为 MP4、GIF、iPhone 实况包或安卓动态照片。保留播放节奏，无需登录。",
        h1="Pixiv 动图，<br>把帧包变成 <em>GIF</em>。",
        lead="Ugoira 不是普通 GIF。下载帧包与时序文件，或直接交给转换器，制作 GIF、实况照片和安卓动态照片。",
        keywords="Pixiv动图下载,Ugoira转GIF,Pixiv转实况,Ugoira MP4",
        placeholder="粘贴 Pixiv Ugoira 作品链接…",
        nav_label="Pixiv 动图转 GIF",
        body=[
            (
                "帧包需要配合时序",
                "ZIP 保存每一帧，JSON 保存每帧停留时长。两份文件一起保留才能还原动画；转换器按实际时序准备视频。",
            ),
            (
                "帧包尺寸与原图尺寸",
                "平台播放帧包可能小于作者的独立原始帧。这里下载平台返回的帧包，不按文件名推断原始画质。当前转换上限为 600 帧、30 秒，并受文件和像素限制。",
            ),
        ],
        faq=[("静态插画可以转动图吗？", "静态作品直接保存原图；只有平台提供逐帧动画的 Ugoira 才显示动画转换入口。")],
        guides=["pixiv-ugoira-to-gif", "pixiv-original-images"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="pixiv-sketch",
        title="Pixiv Sketch原图下载_公开帖子图片提取 - 求原图",
        description="粘贴 Pixiv Sketch 公开帖子或作者链接，按页选择帖子，保存平台提供的原始图片。不需要 Pixiv 账号。",
        h1="Sketch 里的画，<br>按原始格式保存。",
        lead="复制 Sketch 公开帖子或作者主页链接。目录按页展示，点选帖子后保存原始图片。",
        keywords="Pixiv Sketch下载,Sketch原图,Pixiv Sketch图片保存",
        placeholder="粘贴 sketch.pixiv.net 帖子或作者链接…",
        nav_label="Pixiv Sketch 原图",
        body=[
            (
                "公开图片与作者目录",
                "只提取公开帖子的图片，不包含直播、私密或年龄受限内容。作者目录可以翻页，图片逐张保存。",
            )
        ],
        faq=[
            (
                "Sketch 和 Pixiv 主站是同一个入口吗？",
                "两者使用各自的公开接口。这个页面用于 sketch.pixiv.net；主站插画、漫画、小说和动图请使用 Pixiv 工具。",
            )
        ],
        guides=["pixiv-original-images"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="discord-stickers",
        title="Discord动态贴纸下载_APNG与Lottie转透明GIF - 求原图",
        description="粘贴 Discord 公开贴纸包或贴纸素材链接，下载 APNG、Lottie JSON 原文件，转透明 GIF、iPhone 实况包和安卓动态照片。匿名读取官方公开目录，无需 Token。",
        h1="Discord 贴纸，<br>换个地方接着动。",
        lead="从官方公开目录提取贴纸，下载 APNG 与 Lottie 原文件，或转成透明 GIF、实况与动态照片。不需要 Discord 账号或 Bot Token。",
        keywords="Discord贴纸下载,Discord动态贴纸,APNG转GIF,Lottie转GIF,Discord sticker downloader",
        placeholder="粘贴 Discord 贴纸包商店链接或 stickers 素材链接…",
        nav_label="Discord 贴纸",
        body=[
            (
                "公开贴纸包与单个贴纸",
                "支持官方公开目录中的贴纸包商店链接、贴纸包编号链接，以及对应的 stickers 素材链接。按目录中的真实格式下载，不读取聊天记录或私人服务器。",
            ),
            (
                "APNG 与 Lottie 转 GIF",
                'APNG 是会动的 PNG，下载后相册可能只显示首帧；Lottie 是 JSON 动画，点「转 GIF」后可预览。透明 GIF 保留背景透明，实况与动态照片使用普通视频画面。<a href="/guide/discord-stickers-to-gif">查看保存与转换教程</a>。',
            ),
        ],
        faq=[
            (
                "为什么有些 Discord 贴纸无法解析？",
                "这个入口只匹配 Discord 官方当前公开目录中的素材。私人服务器、聊天消息、表情符号和目录外的贴纸暂不支持；不需要为此上传账号 Token。",
            ),
            (
                "保存贴纸后能在 Discord 中发送吗？",
                "这里只下载和转换公开素材。Discord 内的贴纸发送权限仍由账号、服务器和订阅规则决定。",
            ),
        ],
        guides=["discord-stickers-to-gif", "telegram-tgs-to-gif"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="telegram-stickers",
        title="Telegram贴纸下载_TGS转GIF与实况照片 - 求原图",
        description="粘贴 Telegram 贴纸包链接，提取公开目录已收录的 TGS 与 WebP 素材。TGS 可转透明 GIF、iPhone 实况包和安卓动态照片，无需账号或 Bot Token。",
        h1="Telegram 贴纸，<br>保存后接着动。",
        lead="粘贴 addstickers 贴纸包链接，读取公开目录已收录的素材。下载 TGS 原文件，或选一个表情转成透明 GIF、实况与动态照片。",
        keywords="Telegram贴纸下载,TGS转GIF,Telegram动态贴纸,透明GIF,Telegram sticker downloader",
        placeholder="粘贴 Telegram 贴纸包链接，如 https://t.me/addstickers/HotCherry",
        nav_label="Telegram 贴纸",
        body=[
            (
                "公开收录的贴纸包",
                "素材来自 Stickers.wiki 公开目录，不需要在本站登录 Telegram。目录未收录、已经下架或版本不同的贴纸包可能无法取得；结果会标明来源。",
            ),
            (
                "TGS 转透明 GIF",
                "每个动态贴纸可单独下载 TGS，或点「转 GIF」。GIF 保留透明背景；实况与动态照片使用普通视频画面。<a "
                'href="/guide/telegram-tgs-to-gif">查看贴纸保存与转换教程</a>。',
            ),
        ],
        faq=[
            (
                "为什么有些贴纸包没有结果？",
                "匿名读取依赖公开目录已经收录的版本，无法覆盖全部 Telegram 贴纸包。请核对 addstickers 链接；这个入口目前不提供聊天记录、自定义表情包或私人素材下载。",
            )
        ],
        guides=["telegram-tgs-to-gif", "wechat-gif-1mb"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="wechat-channels",
        title="微信视频号视频下载_匿名解析分享链接与封面图片 - 求原图",
        description="粘贴视频号 sph 分享链接，匿名解析视频与封面，支持下载、转 GIF、实况与动态照片。官方预览核对作品，BugPk 公益接口补充视频，无需微信或元宝凭证。",
        h1="视频号的片段，<br>下载或做成实况。",
        lead="复制视频号的 sph 分享链接，下载公开视频与封面。选一小段转 GIF、iPhone 实况包或安卓动态照片，不需要账号凭证。",
        keywords="微信视频号视频下载,视频号封面下载,视频号转GIF,视频号实况照片,视频号分享链接解析",
        placeholder="粘贴视频号分享链接，如 https://weixin.qq.com/sph/…",
        nav_label="微信视频号",
        body=[
            (
                "视频与封面分别保存",
                "标题、作者与封面来自视频号官方公开预览。视频由 BugPk 公益接口匿名补充，核对标题与作者一致后才展示，不要求微信或元宝 Cookie。",
            ),
            (
                "下载后也能转换",
                '有可用视频时，可直接下载或截取片段转换。公益接口不可用或作品核对失败时保留官方封面，并明确提示。<a href="/guide/wechat-channels-download">查看视频下载与转换教程</a>。',
            ),
        ],
        faq=[
            (
                "为什么能显示封面，却没有视频下载？",
                "官方匿名预览可能只开放封面，视频另依赖公益接口当前可用性。失效、受限或无法核对的作品可能只有封面；不保证私人内容、付费内容与直播回放，也不承诺固定清晰度。",
            )
        ],
        guides=["wechat-channels-download", "video-to-live-photo"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="wuthering-waves-wallpaper",
        title="鸣潮国际服启动器壁纸下载_动态背景转GIF与实况 - 求原图",
        description="下载鸣潮国际服当前启动器的公开壁纸与背景视频。静态首帧按原图保存，动态背景可截取片段，转 GIF、iPhone 实况包或安卓动态照片。",
        h1="鸣潮启动器的风景，<br>也能随身收藏。",
        lead="选择鸣潮国际服，读取当前启动器公开背景。下载原图或视频，也可以选一小段做成 GIF 与实况照片。",
        keywords="鸣潮壁纸下载,鸣潮启动器动态背景,Wuthering Waves wallpaper,鸣潮动态壁纸转GIF",
        nav_label="鸣潮壁纸",
        body=[
            (
                "当前国际服背景",
                "工具跟随启动器当前配置，展示实际提供的图片与视频。版本更新后素材可能变化；不提供国服或历史版本壁纸档案。",
            ),
            (
                "选一段，做成动态照片",
                "动态背景点「转 GIF」或「做成实况照片」，准备完成后选择片段。安卓用户可切换动态照片；<a "
                'href="/guide/wuthering-waves-wallpaper-download">查看保存与导入步骤</a>。',
            ),
        ],
        faq=[
            (
                "每个版本都有动态壁纸吗？",
                "取决于启动器当前提供的素材。只有图片时只显示原图下载，视频存在时才提供动态转换。",
            )
        ],
        guides=["wuthering-waves-wallpaper-download", "video-to-live-photo"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="miyoushe-images",
        title="米游社帖子原图下载_高清图集与动态GIF保存 - 求原图",
        description="粘贴米游社单篇帖子链接，提取正文图片并按原始格式逐张保存。支持原神、星穹铁道、绝区零等社区帖子，GIF 保留动效，也能继续转换。",
        h1="米游社里的好图，<br>按原格式保存。",
        lead="复制单篇帖子的完整分享链接。提取正文图集，逐张下载原图；帖子里的 GIF 还能转成实况或安卓动态照片。",
        keywords="米游社原图下载,米游社图集保存,原神壁纸原图,星穹铁道壁纸下载,米游社GIF保存",
        placeholder="粘贴米游社帖子链接，如 https://www.miyoushe.com/ys/article/…",
        nav_label="米游社原图",
        body=[
            (
                "提取正文，不混入头像与推荐图",
                "按当前帖子的正文图片列表保留顺序与原始地址，不把作者头像、单独的封面或推荐帖图片混进图集。图片尺寸以平台提供的文件为准。",
            ),
            (
                "下载后保留真实图片格式",
                'PNG、JPEG、GIF 按文件实际类型保存。动态 GIF 可以继续转换，静态图直接下载；<a href="/guide/miyoushe-original-images">查看米游社原图保存教程</a>。',
            ),
        ],
        faq=[
            (
                "支持作者主页、评论区或 HoYoLAB 吗？",
                "目前支持米游社单篇公开帖子的正文图集，不批量抓取作者主页、评论区，也不把 HoYoLAB 当作已支持的平台。纯视频帖没有正文图片时会提示无图。",
            )
        ],
        guides=["miyoushe-original-images", "hoyoplay-wallpaper-download"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="bilibili-assets",
        title="B站装扮与收藏集公开素材下载_动态卡片转GIF - 求原图",
        description="粘贴 B 站装扮或收藏集详情链接，下载公开展示的背景、卡片图片与动态视频。每份素材独立预览、下载，动态素材可转 GIF、实况与动态照片。",
        h1="B站的动态卡片，<br>下载后继续创作。",
        lead="粘贴装扮或收藏集详情页的完整链接，读取当前公开素材。选中喜欢的图片或视频，单独下载，也能把动态视频做成 GIF。",
        keywords="B站装扮下载,B站收藏集动态卡片下载,B站背景视频,Bilibili动态卡片转GIF",
        placeholder="粘贴 B 站装扮或收藏集详情页完整链接…",
        nav_label="B站装扮与收藏集",
        body=[
            (
                "图片和视频分别展示",
                "同一套装里的背景、卡片和表情是不同素材，每份都可单独下载。动态视频旁可直接打开转换器，不需要把整个套装先下载到电脑。",
            ),
            (
                "以公开展示的素材为准",
                '只读取平台公开返回的图片与视频，不解锁账号权益，也不提供客户端私有缓存中的素材。<a href="/guide/bilibili-collection-assets">查看链接格式与转换教程</a>。',
            ),
        ],
        faq=[
            (
                "为什么收藏集链接缺少参数会失败？",
                "收藏集详情链接需要同时保留 act_id 与 lottery_id，装扮链接需要 id。请打开详情页复制完整网址；首页、搜索页和缩短后的不完整链接不对应具体素材。",
            ),
            (
                "所有装扮动画都能下载吗？",
                "不能。只显示当前公开接口提供的图片与视频；专用动画包和账号内素材不在范围内。视频链接可能过期，下载失败时可重新解析。",
            ),
        ],
        guides=["bilibili-collection-assets", "wechat-gif-1mb"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="steam-assets",
        title="Steam动态头像与头像框下载_APNG转GIF - 求原图",
        description="粘贴 Steam 点数商店单个物品链接，下载动态头像、透明头像框、贴纸与背景视频。保留 GIF、APNG、MP4 或 WebM，动态图片还能转 GIF 与实况照片。",
        h1="Steam 动态素材，<br>原样保存。",
        lead="打开点数商店里的单个头像、头像框、贴纸或背景，复制地址栏链接。动态 PNG 也能保存动效，按需转成 GIF。",
        keywords="Steam动态头像下载,Steam头像框下载,Steam点数商店素材,APNG转GIF,Steam透明头像框,Steam背景视频下载",
        placeholder="粘贴 Steam 点数商店单个物品链接…",
        nav_label="Steam 动态素材",
        body=[
            (
                "能下载哪些 Steam 素材",
                "动态头像通常是 GIF，头像框和贴纸可能是扩展名为 .png 的 APNG；背景视频按平台提供的 MP4 或 WebM 保存。只读取公开素材，不需要登录 Steam。",
            ),
            (
                "PNG 不动时怎么办",
                "部分相册不播放 APNG，但文件本身仍含多帧。下载原图保留原格式，或点「转 GIF」提高兼容性。GIF 支持透明背景；实况照片使用普通视频画面。<a "
                'href="/guide/steam-animated-assets">查看 Steam 动态素材保存教程</a>。',
            ),
        ],
        faq=[
            (
                "需要购买物品才能下载吗？",
                "提取的是点数商店公开展示的素材，不校验购买状态。保存文件不会解锁 Steam 账号里的头像、头像框或背景。",
            ),
            (
                "为什么商店分类页无法解析？",
                "请先打开单个物品，复制带 /reward/ 和物品编号的链接。分类列表与游戏主页不对应单个素材。",
            ),
        ],
        guides=["steam-animated-assets", "wechat-gif-1mb"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="hoyoplay-wallpaper",
        title="HoYoPlay壁纸下载_原神星穹铁道绝区零动态背景 - 求原图",
        description="选择原神、崩坏星穹铁道、绝区零或崩坏3，提取当前国服 HoYoPlay 启动器公开壁纸。静态原图直接保存，动态背景可下载视频、转 GIF 或实况照片。",
        h1="启动器里的风景，<br>留在你的相册。",
        lead="选择游戏，提取当前国服 HoYoPlay 启动器的公开背景。静态壁纸直接下载，有动态视频时也能继续转 GIF 与实况照片。",
        keywords="HoYoPlay壁纸下载,原神启动器壁纸,星穹铁道动态壁纸,绝区零启动器背景,崩坏3壁纸,米哈游启动器壁纸",
        nav_label="HoYoPlay 壁纸",
        body=[
            (
                "当前版本的公开壁纸",
                "支持原神、崩坏：星穹铁道、绝区零与崩坏 3 的国服启动器背景。页面每次解析当前公开数据；同一游戏可能有多张图片，动态视频是否提供取决于当前版本。",
            ),
            (
                "从壁纸到 GIF 与实况",
                "原图保留平台实际格式；动态背景可下载 WebM，也可选一小段转换成 GIF、iPhone 实况包或安卓动态照片。<a "
                'href="/guide/hoyoplay-wallpaper-download">查看下载与导入教程</a>。',
            ),
        ],
        faq=[
            (
                "可以下载历史版本的启动器壁纸吗？",
                "这里提取当前公开接口返回的素材，不提供历史壁纸档案。版本更新后，结果可能变化。",
            ),
            (
                "为什么有些游戏只显示图片？",
                "不是每个版本都提供动态背景。平台没有返回视频时，只提供原图下载，不会把静态图伪装成动态壁纸。",
            ),
        ],
        guides=["hoyoplay-wallpaper-download", "video-to-live-photo"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="apple-music-cover",
        title="Apple Music封面下载_动态专辑封面转GIF - 求原图",
        description="粘贴 Apple Music 歌曲、专辑或歌单链接，下载高清封面。作品提供公开动态封面时，可下载 MP4，或转换为 GIF、实况照片与安卓动态照片。",
        h1="Apple Music 封面，<br>静态与动态都能留。",
        lead="复制歌曲、专辑或歌单的网页链接，下载封面图片；作品有公开动态封面时，另提供视频下载和 GIF、实况转换。",
        keywords="Apple Music封面下载,Apple Music动态封面,苹果音乐专辑封面原图,Apple Music artwork downloader,动态专辑封面转GIF",
        placeholder="粘贴 music.apple.com 歌曲、专辑或歌单链接…",
        nav_label="Apple Music 封面",
        body=[
            (
                "封面图片与动态视频分开保存",
                "「下载封面」保存静态图片；有公开动态版本时，「下载动态封面」提供方形或竖版 MP4。没有动态版本的作品只显示图片下载，不需要 Apple Music 订阅或在本站登录。",
            ),
            (
                "动态封面继续转 GIF",
                '点「转 GIF」或「做成实况照片」，等待视频准备完成后选择片段。只提取网页公开封面，不下载歌曲音频。QQ 音乐与网易云的封面可从<a href="/qq-music-cover">QQ 音乐</a>、<a '
                'href="/netease-music-cover">网易云音乐</a>工具进入。',
            ),
        ],
        faq=[
            (
                "每张专辑都有动态封面吗？",
                "不是。是否提供、方形或竖版有哪些版本，由作品当前的公开网页决定；不同地区或时间可能不同。",
            ),
            (
                "歌曲链接会提取什么？",
                "通常是歌曲所属专辑的封面。歌单链接提取歌单本身的封面，不会逐首下载歌单内的歌曲图片。",
            ),
        ],
        guides=["music-cover-download", "video-to-live-photo"],
        common=["free", "ratelimit"],
    ),
    Page(
        slug="",
        title="求原图 - 抖音小红书原图和无水印视频提取，视频转 GIF 和实况照片",
        description="粘贴分享链接，提取抖音、小红书等平台的无水印视频与原图，下载 QQ 音乐、网易云歌单和专辑封面。动态封面与视频可转 GIF、实况照片，免费不用登录。",
        h1="把视频定格成 <em>GIF</em>、<br>实况与原图。",
        lead="抖音、小红书、快手、YouTube、X 里复制的整段分享文字直接贴进来就行。视频没有水印，图片是原图。",
        keywords="求原图,原图下载,视频提取,图片提取,去水印,视频转GIF,视频转实况照片,视频定格,定格,抖音,小红书,快手,YouTube,推特",
        nav_label="首页",
        common=ALL_COMMON,  # 首页承载完整问答，落地页各挑最相关的 3 条
        guides=["xiaohongshu-live-photo-iphone", "video-to-gif", "video-to-live-photo"],
    ),
    Page(
        slug="netease-music-cover",
        title="网易云音乐封面提取_歌单专辑原图下载_动态封面转GIF - 求原图",
        description="粘贴网易云音乐歌曲、专辑或歌单分享链接，在线提取封面原图。动态 WebP 封面保留原格式，也能转 GIF、iPhone 实况或安卓动态照片。免费不用登录。",
        h1="网易云音乐封面，<br>会动的也能带走。",
        lead="把歌曲、专辑或歌单链接贴进来，拿到对应的封面图片。歌单取歌单自己的封面；动态封面可直接下载，也能继续转 GIF 与实况照片。",
        keywords="网易云音乐封面提取,网易云歌单封面下载,网易云专辑封面原图,网易云动态封面下载,动态WebP转GIF,歌单封面转实况",
        placeholder="粘贴网易云歌曲、专辑或歌单分享链接…",
        nav_label="网易云封面下载",
        body=[
            (
                "歌单封面，不是第一首歌",
                "歌曲链接提取歌曲对应的专辑封面，专辑链接取专辑封面，歌单链接取歌单本身的封面。无需逐首解析，也不会把歌单里的第一首歌曲当成歌单封面。",
            ),
            (
                "下载动态封面，保留原格式",
                "封面可能是 JPEG、PNG、GIF 或 WebP，以平台返回的实际文件为准。地址以 .jpg 结尾也可能是动态 WebP，下载时按真实格式保存，不把动图变成静态截图。原图尺寸取决于平台提供的资源，不承诺固定高清尺寸。",
            ),
            (
                "把喜欢的动态封面做成 GIF",
                '解析后点「转 GIF」，可调整片段、帧率和大小；也可点「做成实况照片」，或在转换器切换安卓动态照片。<a href="/guide/music-cover-download">查看封面下载与转换教程</a>。',
            ),
        ],
        faq=[
            (
                "网易云动态封面为什么下载成 WebP？",
                "WebP 可以包含多帧动画。平台返回动态 WebP 时，下载会保留原文件和动效；如果接收应用不支持 WebP，可在这里转成 GIF。修改扩展名并不会转换文件格式。",
            ),
            (
                "能下载歌单里的全部歌曲封面或音乐吗？",
                "这里提取分享链接对应的一张封面，不批量下载歌单内每首歌曲的封面，也不下载歌曲音频。私密、已删除或不可公开访问的内容可能无法提取。",
            ),
        ],
        common=["iphone", "free"],
        guides=["music-cover-download", "wechat-gif-1mb", "video-to-live-photo"],
    ),
    Page(
        slug="qq-music-cover",
        title="QQ音乐封面提取_歌曲专辑歌单高清封面下载_免登录 - 求原图",
        description="在线提取 QQ 音乐歌曲、专辑和歌单封面，粘贴分享链接即可下载。歌曲与专辑封面可取 800px，歌单封面可取 1000px，以平台资源为准。免费不用登录。",
        h1="QQ 音乐封面，<br>贴链接，取原图。",
        lead="在 QQ 音乐里复制歌曲、专辑或歌单分享链接，直接贴进来。预览对应封面，再按图片实际格式下载，不用安装插件。",
        keywords="QQ音乐封面提取,QQ音乐封面下载,QQ音乐歌单封面,QQ音乐专辑封面原图,QQ音乐高清封面,QQ音乐歌曲封面保存",
        placeholder="粘贴 QQ 音乐歌曲、专辑或歌单分享链接…",
        nav_label="QQ 音乐封面下载",
        body=[
            (
                "歌曲、专辑和歌单都支持",
                "从 QQ 音乐 App 的分享菜单复制链接，或者使用网页版作品地址。歌曲取其专辑封面；歌单取自己的封面，不会改为列表里第一首歌的图片。",
            ),
            (
                "封面能有多清晰",
                "歌曲与专辑封面可取 800×800，歌单封面可取 1000×1000，下载尺寸以平台提供的图片为准，保留实际格式。",
            ),
            (
                "下载后还能做什么",
                '静态封面按 JPEG、PNG 等实际格式直接保存。平台返回多帧图片时，可继续转 GIF 或实况照片。网易云动态歌单封面也可用<a href="/netease-music-cover">网易云封面工具</a>提取。',
            ),
        ],
        faq=[
            (
                "QQ 音乐封面提取需要登录或安装插件吗？",
                "公开的歌曲、专辑和歌单封面无需在本站登录，也不用安装浏览器插件；复制分享链接即可。作品下架、设为私密或平台限制访问时可能不可用。",
            ),
            (
                "QQ 音乐封面支持转 GIF 或实况吗？",
                "取决于平台返回的图片。静态封面直接下载即可；平台提供多帧动态封面时，可使用 GIF、实况或动态照片转换功能。",
            ),
        ],
        common=["ratelimit", "free"],
        guides=["music-cover-download", "video-to-gif"],
    ),
    Page(
        slug="douyin",
        title="抖音去水印下载_无水印视频_图集原图_实况图提取 - 求原图",
        description="在线解析抖音分享链接：无水印视频（含 H.265 高清档）、图集无水印原图、实况图打包成 iPhone 实况或安卓动态照片、背景音乐。免费不用登录。",
        h1="抖音去水印，<br>图集与实况一键拿走。",
        lead="打开抖音，点分享 → 复制链接，把整段文字贴进来。视频无水印，图集是无水印原图，实况图可以直接打包成 iPhone 实况。",
        keywords="抖音去水印,抖音视频下载,抖音图集下载,抖音实况图保存,抖音背景音乐提取,抖音无水印解析",
        placeholder="粘贴抖音分享链接，如 https://v.douyin.com/xxxx/",
        nav_label="抖音去水印",
        faq=[
            (
                "抖音实况图能保存吗？",
                '能。图集里带实况的图片会标出来，点"iPhone 实况"打包成配对好的 JPG + MOV，点"安卓动态照片"得到内嵌视频的 JPG，多张实况一次打包。',
            ),
            (
                "为什么有的抖音视频只有 720p？",
                '抖音网页端只对部分视频提供 1080p。有更高清晰度或 H.265 版本时，"其他清晰度"菜单里会列出来。',
            ),
        ],
        body=[
            (
                "抖音链接能拿到什么",
                "视频帖子：无水印 MP4（H.264，浏览器可直接播放），有更高档位时列出 H.265 版本和文件大小，另有背景音乐 MP3。图集帖子：每张图的无水印原始尺寸 JPEG，不是 App 里带用户名水印的那份；带实况的图片连同 2–3 秒的短视频一起，可打包成 iPhone 实况照片或安卓动态照片。",
            ),
            (
                "三步操作",
                "在抖音 App 里点分享 → 复制链接；把整段文字贴进上面的输入框（网址会自动识别）；点解析，选择要保存的内容。整个过程不需要登录抖音，也不需要安装任何软件。",
            ),
            (
                "哪些链接不行",
                '直播间、私密作品、仅好友可见的作品，平台不会返回数据，会提示"已删除或设为私密"。个人主页链接不支持，只解析单个作品。',
            ),
        ],
        common=["watermark", "iphone", "free"],
        guides=["douyin-no-watermark", "douyin-image-post-original", "douyin-live-photo", "douyin-long-video-h265"],
    ),
    Page(
        slug="xiaohongshu",
        title="小红书图片原图下载_视频去水印_实况图保存到相册 - 求原图",
        description="粘贴小红书分享链接，取出笔记原图（原始分辨率）、无水印视频和实况图。实况图可打包成 iPhone 实况照片或安卓动态照片。免费不用登录。",
        h1="小红书原图、视频与实况，<br>整篇取出来。",
        lead="小红书 App 里点分享 → 复制链接，贴进来就行。图片是原始分辨率，视频无水印，实况图直接打包。",
        keywords="小红书图片下载,小红书原图,小红书视频去水印,小红书实况图保存,小红书笔记图片提取,xhslink 解析",
        placeholder="粘贴小红书分享链接，如 http://xhslink.com/xxxx",
        nav_label="小红书图片下载",
        faq=[
            (
                "小红书链接提示已过期？",
                "小红书分享链接里带一个有时效的 token，过期后平台会拒绝访问。回到 App 重新复制一次链接即可。",
            ),
            (
                "小红书原图是什么格式？",
                "小红书存的原始文件是 HEIC，浏览器打不开，所以这里给的是原始分辨率的 JPEG（质量 90），比 App 里长按保存的更清晰。",
            ),
        ],
        body=[
            (
                "小红书笔记能拿到什么",
                "图文笔记：每张图按原始分辨率取（常见 1920×2560），比 App 长按保存的 1080 宽版本更清晰；带实况的图片会标出来，可以整篇打包成 iPhone 实况或安卓动态照片。视频笔记：无水印 MP4，多档清晰度时选最高的。",
            ),
            (
                "链接必须是新鲜的",
                "小红书的分享链接带一个 xsec_token，缺了或者过期就打不开。几天内复制的链接实测多半还能用，失败了就回 App 重新复制一次，整段文字贴进来。xhslink.com 短链和网页版分享出来的长链接都可以。",
            ),
            (
                "和 App 内保存的区别",
                "App 内长按保存的是压缩后的 1080 宽图片，实况图只剩静态一帧。通过链接解析拿到的是平台存储的原始尺寸，实况图连视频一起。",
            ),
        ],
        common=["watermark", "iphone", "links"],
        guides=["xiaohongshu-live-photo-iphone", "xiaohongshu-link-expired", "video-to-live-photo"],
    ),
    Page(
        slug="kuaishou",
        title="快手视频去水印下载_图集原图提取_转 GIF 和实况 - 求原图",
        description="粘贴快手分享链接，在线下载无水印视频和图集原图，可截一段转 GIF 或实况照片。免费不用登录。",
        h1="快手去水印，<br>视频图集直接存。",
        lead="快手 App 里点分享 → 复制链接，把整段文字贴进来。",
        keywords="快手去水印,快手视频下载,快手图集下载,快手无水印解析",
        placeholder="粘贴快手分享链接，如 https://v.kuaishou.com/xxxx",
        nav_label="快手去水印",
        faq=[
            (
                "快手长视频能解析吗？",
                "能。快手的短视频、长视频和图集用的是同一套分享链接，贴进来都能认，不用区分类型。",
            ),
            (
                "为什么有的快手作品打不开？",
                "私密作品、已删除的作品和直播回放拿不到数据。另外快手的分享链接有时效，隔太久再用可能已经失效，回 App 重新复制一次就好。",
            ),
        ],
        body=[
            (
                "快手链接能拿到什么",
                "视频作品：无水印 MP4，不带右下角的用户号水印。图集作品：每张图按原始尺寸列出来，可以逐张保存。解析完还能直接截一段做 GIF 或实况照片。",
            ),
            (
                "怎么复制链接",
                "在快手 App 里点右侧的分享按钮 → 复制链接，会得到 v.kuaishou.com 开头的短链，整段文字贴进输入框即可，不用手动摘出网址。网页版 kuaishou.com 的长链接同样支持。",
            ),
            (
                "和 App 内保存的区别",
                "快手 App 里保存的视频右下角带用户号水印，图集只能一张张长按。通过链接解析拿到的是无水印的原始文件，图集一次列全，逐张保存。",
            ),
        ],
        common=["watermark", "links", "free"],
        guides=["kuaishou-download", "video-to-gif", "video-to-live-photo"],
    ),
    Page(
        slug="youtube",
        title="YouTube 视频下载_1080p 4K_仅音频 - 求原图",
        description="粘贴 YouTube 链接，在线下载 1080p、1440p、4K 视频或仅音频 m4a，还能截一段做 GIF 或实况照片。免费不用登录。",
        h1="<em>YouTube</em> 下载，<br>1080p 到 4K 都能选。",
        lead="把 YouTube 视频链接贴进来，清晰度列表里选一个，服务器合并音视频后给你一个完整的 MP4。",
        keywords="YouTube视频下载,油管下载,YouTube转MP4,YouTube转MP3,YouTube 1080p下载",
        placeholder="粘贴 YouTube 链接，如 https://youtu.be/xxxx",
        nav_label="YouTube 下载",
        faq=[
            (
                "YouTube 高清下载为什么要等一会儿？",
                "1080p 以上 YouTube 把画面和声音分开存，服务器要分别下载再合并，视频越长等得越久。360p / 720p 的合一版本可以直接播放和保存。",
            ),
        ],
        body=[
            (
                "YouTube 链接能拿到什么",
                "360p 的合一版本可以直接播放和保存；720p、1080p、1440p、4K 由服务器下载并合并音视频后提供完整 MP4；「仅音频」给原始 m4a 音轨。清晰度菜单里会显示每档的预估大小。",
            ),
            (
                "等待时间",
                "高清档位需要服务器先下载再合并，10 分钟的 1080p 视频通常在一分钟内完成，4K 长视频可能要几分钟。页面上有进度条，完成后点保存即可。",
            ),
        ],
        common=["free", "ratelimit", "gifsize"],
        guides=["youtube-1080p-download", "video-to-gif"],
    ),
    Page(
        slug="x",
        title="X (Twitter) 视频下载_推文图片原图 - 求原图",
        description="粘贴 X / Twitter 推文链接，在线下载推文里的视频（最高码率）和图片原图，可转 GIF 或实况照片。免费不用登录。",
        h1="<em>X</em> 推文里的视频与图片，<br>直接保存。",
        lead="复制推文链接（x.com 或 twitter.com 都行）贴进来。视频取最高码率的 MP4，图片是原图。",
        keywords="推特视频下载,Twitter视频下载,X视频下载,推特图片原图,twitter gif 下载",
        placeholder="粘贴推文链接，如 https://x.com/user/status/xxxx",
        nav_label="X 视频下载",
        faq=[
            (
                "推文里的 GIF 下载下来为什么是 MP4？",
                'X 在上传时就把 GIF 转成了循环播放的 MP4，平台本身不再保存 GIF 原文件，所以这里拿到的也是 MP4。确实需要 GIF 的话，解析后点"转 GIF"再转一次即可。',
            ),
            (
                "需要登录才能看的推文能解析吗？",
                '能，而且不需要你登录 X。标记为敏感内容、或者提示"登录后查看"的推文会自动换一条通道取数据。已删除、账号已注销、设为仅关注者可见的推文拿不到。',
            ),
        ],
        body=[
            (
                "推文能拿到什么",
                "视频：从所有码率版本里挑最高的那个 MP4。图片：多图推文把每张都列出来，按原图尺寸保存，不是时间线上压缩过的缩略图。GIF：以 MP4 形式给出，可以再转成真正的 GIF 文件。",
            ),
            (
                "x.com 和 twitter.com 都认",
                "改名前后的两种域名都支持，App 分享出来的带一串参数的链接也能直接贴。复制地址栏里的推文链接，或者点推文下方的分享 → 复制链接，整段贴进来即可。",
            ),
            (
                "常见用法",
                '把推文里的短视频做成表情包：解析后点"转 GIF"，拖出想要的几秒。保存多图推文的全部原图：解析后逐张保存。把一段视频做成 iPhone 实况照片：点"做成实况照片"，自己选段和封面帧。',
            ),
        ],
        common=["watermark", "links", "free"],
        guides=["video-to-gif"],
    ),
    Page(
        slug="bilibili",
        title="B站视频下载_1080p 高清_仅音频提取_b23 短链解析 - 求原图",
        description="粘贴 B站 视频链接，在线下载 1080p / 720p 视频或仅音频，可截一段做 GIF 或实况照片。b23.tv 短链也支持。免费不用登录。",
        h1="B站视频下载，<br>1080p 高清直接选。",
        lead="把 B站 链接（bilibili.com 或 b23.tv）贴进来，清晰度列表里选一档。",
        keywords="B站视频下载,bilibili下载,哔哩哔哩视频保存,B站1080p下载,B站音频提取",
        placeholder="粘贴 B站 链接，如 https://b23.tv/xxxx",
        nav_label="B站下载",
        faq=[
            (
                "为什么默认只给 480p？",
                '网页端接口直接返回的播放地址就是 480p 的合一版本，点开立刻能播能存。720p 和 1080p 在 B站 是画面与声音分开存的，要服务器分别下载再合并，所以单列在"其他清晰度"里，点了需要等一会儿。',
            ),
            (
                "多 P 视频会解析哪一集？",
                "固定解析第一 P。链接里的 p= 参数目前不生效，所以合集、课程这类多 P 投稿只能拿到第 1 集。",
            ),
        ],
        body=[
            (
                "B站链接能拿到什么",
                "默认给一个可直接播放的 480p MP4；720p、1080p 和仅音频由服务器下载合并后提供完整文件。BV 号完整链接、b23.tv 短链、手机 App 分享出来的链接都能直接贴。",
            ),
            (
                "支持哪些链接",
                "只支持普通投稿视频，也就是网址里带 /video/BV… 的那种。番剧、影视、课堂和直播回放（/bangumi/、/cheese/ 等路径）不在支持范围内。多 P 投稿固定取第一 P。",
            ),
            (
                "清晰度说明",
                "不登录的情况下最高到 1080p。4K、HDR、杜比这些档位属于大会员专属，网页端不登录拿不到，这里也就不提供。仅音频档给的是从视频里分离出来的原始音轨，适合存 BGM 或者课程录音。",
            ),
        ],
        common=["free", "ratelimit", "links"],
        guides=["bilibili-video-download", "video-to-gif", "video-to-live-photo"],
    ),
    Page(
        slug="gif",
        title="视频转 GIF 在线工具_抖音小红书链接直接转 - 求原图",
        description="在线把视频转成 GIF：粘贴抖音、小红书、YouTube 链接或上传本地视频，拖缩略图条选段，可调帧率、宽度、速度和抖动。免费不用登录。",
        h1="视频转 <em>GIF</em>，<br>选一段就好。",
        lead="贴一个视频链接，或者上传本地视频。在缩略图条上拖出要的那几秒，帧率、宽度、速度都能调。",
        keywords="视频转GIF,在线GIF制作,抖音视频转GIF,MP4转GIF,GIF压缩,GIF制作工具",
        nav_label="视频转 GIF",
        faq=[
            ("GIF 最长能做多长？", "30 秒。GIF 超过 10 秒体积会很大，一般 3–8 秒最好用。"),
            (
                "怎么让 GIF 更清晰或更小？",
                '清晰：把宽度拉大、抖动选"误差扩散"。小：降帧率到 8–10、宽度 320–360、时长缩短。',
            ),
        ],
        body=[
            (
                "怎么用",
                "贴链接解析后点「转 GIF」，或者点右上角「本地视频」上传手机里的视频。30 秒以内的视频直接按默认参数生成，几秒后就能预览；要剪片段或调帧率、宽度、速度，展开「剪片段、调参数」改完重新生成。手机上长按结果图就能存进相册。",
            ),
            (
                "推荐参数",
                "微信表情包：240–320 px、8–10 fps、2–3 秒，体积 1 MB 以内。群聊里看字幕：360–480 px、12 fps。想保留细节：640 px 以上，但体积会很大。",
            ),
        ],
        common=["gifsize", "ratelimit", "free"],
        guides=["video-to-gif", "wechat-gif-1mb", "douyin-no-watermark"],
    ),
    Page(
        slug="live-photo",
        title="视频转实况照片_Live Photo 动态照片在线制作 - 求原图",
        description="在线把视频片段做成 iPhone 实况照片（配对好的 JPG + MOV）或安卓动态照片（内嵌视频的 JPG），支持抖音、小红书链接和本地视频，可选封面帧。",
        h1="视频做成 <em>Live Photo</em>，<br>iPhone 与安卓都行。",
        lead="贴链接或上传视频，选最多 10 秒，挑一帧做封面。iPhone 得到配对好的 JPG + MOV，安卓得到一张会动的 JPG。",
        keywords="视频转实况照片,视频转Live Photo,实况照片制作,动态照片制作,Motion Photo,小红书实况图,抖音实况图",
        nav_label="视频转实况",
        faq=[
            ("实况照片的封面能自己选吗？", '能。转换面板里拖"封面帧"，或者把预览播到想要的画面点"用当前画面做封面"。'),
            (
                "为什么 iPhone 不能直接保存实况？",
                'iOS 只允许 App 往相册写实况，网页、小程序、分享面板存进去都是一张图加一段视频。iPhone 上把生成的视频存相册后用剪映或视频转实况 App 转；有 Mac 就把 zip 里的 JPG 和 MOV 拖进"照片"App。',
            ),
        ],
        body=[
            (
                "两种输出",
                "iPhone 实况照片：一对 JPG + MOV，写入相同的 Apple 内容标识，打包成 zip；拖进 Mac 的「照片」App 就是实况，同步到 iPhone 后长按会动，可设为锁屏动态壁纸。iPhone 上也可以只存视频，再用剪映或视频转实况 App 转。安卓动态照片：一张内嵌 MP4 的 JPG（MVIMG_ 开头），存到相册即可，Google 相册、三星、小米、OPPO、vivo 都识别。",
            ),
            (
                "从哪里来的视频都行",
                "抖音、小红书、YouTube 等链接解析后点「做成实况照片」；本地视频点右上角上传。小红书和抖音图集里自带的实况图不用裁剪，解析后在「保存实况」里直接存。",
            ),
        ],
        common=["iphone", "android", "free"],
        guides=["video-to-live-photo", "android-motion-photo", "xiaohongshu-live-photo-iphone", "douyin-live-photo"],
    ),
]

PAGE_BY_SLUG = {p.slug: p for p in PAGES}


def absolute(base: str, path: str) -> str:
    return (SITE_URL or base).rstrip("/") + path


def _dump(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def _faq_ld(faq: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq
        ],
    }


def _crumbs(base: str, *items: tuple[str, str]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": absolute(base, path)}
            for i, (name, path) in enumerate(items)
        ],
    }


def json_ld(page: Page, base: str) -> str:
    url = absolute(base, page.path)
    data = [
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": SITE_NAME,
            "url": url,
            "description": page.description,
            "applicationCategory": "MultimediaApplication",
            "operatingSystem": "Any",
            "browserRequirements": "Requires JavaScript",
            "inLanguage": "zh-CN",
            "isAccessibleForFree": True,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CNY"},
            "featureList": [
                "无水印视频提取",
                "原图提取",
                "QQ 音乐与网易云封面提取",
                "Steam 动态素材与 HoYoPlay 壁纸提取",
                "Apple Music 静态与动态封面提取",
                "动态封面转 GIF 与实况",
                "视频转 GIF",
                "视频转实况照片",
                "动态照片",
            ],
        },
        _faq_ld(page.all_faq),
    ]
    if not page.slug:
        data.append(
            {
                "@context": "https://schema.org",
                "@type": "WebSite",
                "name": SITE_NAME,
                "url": absolute(base, "/"),
                "inLanguage": "zh-CN",
            }
        )
    else:
        data.append(_crumbs(base, (SITE_NAME, "/"), (page.nav_label, page.path)))
    return _dump(data)


def guide_json_ld(guide: Guide, base: str) -> str:
    url = absolute(base, guide.path)
    data = [
        {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": guide.h1,
            "description": guide.description,
            "inLanguage": "zh-CN",
            "totalTime": "PT3M",
            "tool": [{"@type": "HowToTool", "name": SITE_NAME}],
            "step": [
                {"@type": "HowToStep", "position": i + 1, "name": name, "text": text, "url": f"{url}#step-{i + 1}"}
                for i, (name, text) in enumerate(guide.steps)
            ],
        },
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": guide.title,
            "description": guide.description,
            "inLanguage": "zh-CN",
            "datePublished": guide.published,
            "dateModified": guide.updated,
            "author": {"@type": "Organization", "name": SITE_NAME, "url": absolute(base, "/")},
            "publisher": {"@type": "Organization", "name": SITE_NAME},
            "mainEntityOfPage": url,
        },
        _faq_ld(guide.faq),
        _crumbs(base, (SITE_NAME, "/"), ("教程", "/guides"), (guide.h1, guide.path)),
    ]
    return _dump(data)


def status_json_ld(base: str, faq: list[tuple[str, str]]) -> str:
    return _dump([_faq_ld(faq), _crumbs(base, (SITE_NAME, "/"), ("解析状态", "/status"))])


def guides_index_json_ld(base: str) -> str:
    return _dump([_crumbs(base, (SITE_NAME, "/"), ("教程", "/guides"))])


def sitemap_xml(base: str) -> str:
    def url(path: str, priority: str, freq: str = "weekly", lastmod: str = UPDATED) -> str:
        return (
            f"<url><loc>{absolute(base, path)}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        )

    items = [url(p.path, "1.0" if not p.slug else "0.8") for p in PAGES]
    items.append(url("/guides", "0.7"))
    items += [url(g.path, "0.7", "monthly", g.updated) for g in GUIDES]
    # 状态页每小时自检一次，lastmod 写今天（北京时间）
    items.append(url("/status", "0.6", "hourly", time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(items) + "</urlset>"
    )


def all_paths() -> list[str]:
    """给主动推送脚本用。"""
    return [p.path for p in PAGES] + ["/guides"] + [g.path for g in GUIDES] + ["/status"]
