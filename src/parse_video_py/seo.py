"""SEO 用的落地页定义：同一个工具，按搜索意图给每类需求一个独立 URL、标题和文案。

首页拿"视频图片提取 / GIF / 实况"这些泛词，落地页各拿一个精确词（"抖音去水印"、"视频转GIF"…），
互相内链，并和 guides.py 里的教程互链。文案都是给人看的，不堆关键词。

标题控制在 30 个汉字以内（百度截断位置），描述 80 个汉字以内。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .guides import GUIDES, GUIDE_BY_SLUG, Guide

SITE_NAME = "拾帧"
SITE_URL = os.environ.get("PARSE_VIDEO_SITE_URL", "").rstrip("/")   # 如 https://example.com；不填就按请求地址
UPDATED = "2026-09-20"

# 站长验证 / 统计代码：直接贴 <meta> 或 <script> 片段
SITE_VERIFICATION_HTML = os.environ.get("PARSE_VIDEO_SITE_VERIFICATION", "")
ANALYTICS_HTML = os.environ.get("PARSE_VIDEO_ANALYTICS", "")


@dataclass
class Page:
    slug: str                 # "" 表示首页
    title: str                # <title>，关键词在前、品牌在后
    description: str
    h1: str                   # 允许 <em>（斜体强调的拉丁词）和 <br>
    lead: str
    keywords: str
    placeholder: str = "粘贴分享链接…"
    nav_label: str = ""       # 页脚互链里显示的名字
    faq: list[tuple[str, str]] = field(default_factory=list)   # 这一页专属的问答，排在通用问答前面
    body: list[tuple[str, str]] = field(default_factory=list)  # 这一页独有的正文 (h2, html)，放在工具下方
    guides: list[str] = field(default_factory=list)            # 相关教程 slug
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
    "links": ("支持哪些链接？", "抖音、小红书、快手、微博、B站、西瓜、皮皮虾、AcFun 等国内平台，以及 YouTube、X（Twitter）、TikTok、Instagram 等国外平台。直接粘贴 App 里\"复制链接\"得到的整段文字即可，不用手动摘出网址。"),
    "watermark": ("视频有水印吗？图片是原图吗？", "视频取的是平台无水印的播放地址；小红书图片会换成原图分辨率，抖音图集取的是不带水印的 JPEG 原始尺寸。个别平台只提供 720p 的无水印版本。"),
    "iphone": ("实况照片怎么导入 iPhone？", "下载得到的 zip 里是一对 JPG + MOV，两者写入了相同的 Apple 内容标识。把它们传到手机后，用任意支持\"图片 + 视频合成实况\"的 App 或快捷指令导入相册即可。网页本身无法直接写入 iOS 相册。"),
    "android": ("安卓动态照片怎么用？", "下载的 MVIMG_xxx.jpg 直接保存到手机相册，Google 相册、三星、小米、OPPO、vivo 等相册会识别为动态照片，长按即可播放。"),
    "gifsize": ("GIF 太大怎么办？", "GIF 的体积主要由宽度 × 帧率 × 时长决定。480 像素宽、12 帧、10 秒大约 10 MB；改成 360 像素宽、10 帧、5 秒就只有 2–3 MB。"),
    "ratelimit": ("提示\"请求太频繁\"？", "为了让服务对所有人可用，每个 IP 每分钟的解析次数和同时进行的转换任务数有限制。等一会儿再试就好。"),
    "free": ("需要注册或付费吗？", "不需要。所有功能免费，不用登录，也不会保存你粘贴的链接和下载的文件（临时文件一小时内自动清理）。"),
}

DEFAULT_COMMON = ["watermark", "ratelimit", "free"]
ALL_COMMON = list(COMMON_FAQ)

PAGES: list[Page] = [
    Page(
        slug="",
        title="拾帧 - 抖音小红书视频图片提取，在线转 GIF 和实况照片",
        description="粘贴抖音、小红书、快手、YouTube、X 的分享链接，免费提取无水印视频和原图，一键做成 GIF、iPhone 实况照片或安卓动态照片。不用登录。",
        h1="把链接变成 <em>GIF</em>、<br>实况与原图。",
        lead="抖音、小红书、快手、YouTube、X 里复制的整段分享文字直接贴进来就行。视频没有水印，图片是原图。",
        keywords="视频提取,图片提取,去水印,视频转GIF,视频转实况照片,抖音,小红书,快手,YouTube,推特",
        nav_label="首页",
        common=ALL_COMMON,   # 首页承载完整问答，落地页各挑最相关的 3 条
        guides=["xiaohongshu-live-photo-iphone", "video-to-gif", "video-to-live-photo"],
    ),
    Page(
        slug="douyin",
        title="抖音去水印下载_图集原图_实况图提取 - 拾帧",
        description="在线解析抖音分享链接：无水印视频（含 H.265 高清档）、图集无水印原图、实况图打包成 iPhone 实况或安卓动态照片、背景音乐。免费不用登录。",
        h1="抖音去水印，<br>图集与实况一键拿走。",
        lead="打开抖音，点分享 → 复制链接，把整段文字贴进来。视频无水印，图集是无水印原图，实况图可以直接打包成 iPhone 实况。",
        keywords="抖音去水印,抖音视频下载,抖音图集下载,抖音实况图保存,抖音背景音乐提取,抖音无水印解析",
        placeholder="粘贴抖音分享链接，如 https://v.douyin.com/xxxx/",
        nav_label="抖音去水印",
        faq=[
            ("抖音实况图能保存吗？", "能。图集里带实况的图片会标出来，点\"iPhone 实况\"打包成配对好的 JPG + MOV，点\"安卓动态照片\"得到内嵌视频的 JPG，多张实况一次打包。"),
            ("为什么有的抖音视频只有 720p？", "抖音网页端只对部分视频提供 1080p。有更高清晰度或 H.265 版本时，\"其他清晰度\"菜单里会列出来。"),
        ],
        body=[
            ("抖音链接能拿到什么", "视频帖子：无水印 MP4（H.264，浏览器可直接播放），有更高档位时列出 H.265 版本和文件大小，另有背景音乐 MP3。图集帖子：每张图的无水印原始尺寸 JPEG，不是 App 里带用户名水印的那份；带实况的图片连同 2–3 秒的短视频一起，可打包成 iPhone 实况照片或安卓动态照片。"),
            ("三步操作", "在抖音 App 里点分享 → 复制链接；把整段文字贴进上面的输入框（网址会自动识别）；点解析，选择要保存的内容。整个过程不需要登录抖音，也不需要安装任何软件。"),
            ("哪些链接不行", "直播间、私密作品、仅好友可见的作品，平台不会返回数据，会提示\"已删除或设为私密\"。个人主页链接不支持，只解析单个作品。"),
        ],
        common=["watermark", "iphone", "free"],
        guides=["douyin-no-watermark", "douyin-image-post-original", "video-to-gif"],
    ),
    Page(
        slug="xiaohongshu",
        title="小红书图片原图下载_视频去水印_实况图保存 - 拾帧",
        description="粘贴小红书分享链接，取出笔记原图（原始分辨率）、无水印视频和实况图。实况图可打包成 iPhone 实况照片或安卓动态照片。免费不用登录。",
        h1="小红书原图、视频与实况，<br>整篇取出来。",
        lead="小红书 App 里点分享 → 复制链接，贴进来就行。图片是原始分辨率，视频无水印，实况图直接打包。",
        keywords="小红书图片下载,小红书原图,小红书视频去水印,小红书实况图保存,小红书笔记图片提取,xhslink 解析",
        placeholder="粘贴小红书分享链接，如 http://xhslink.com/xxxx",
        nav_label="小红书图片下载",
        faq=[
            ("小红书链接提示已过期？", "小红书分享链接里带一个有时效的 token，过期后平台会拒绝访问。回到 App 重新复制一次链接即可。"),
            ("小红书原图是什么格式？", "小红书存的原始文件是 HEIC，浏览器打不开，所以这里给的是原始分辨率的 JPEG（质量 90），比 App 里长按保存的更清晰。"),
        ],
        body=[
            ("小红书笔记能拿到什么", "图文笔记：每张图按原始分辨率取（常见 1920×2560），比 App 长按保存的 1080 宽版本更清晰；带实况的图片会标出来，可以整篇打包成 iPhone 实况或安卓动态照片。视频笔记：无水印 MP4，多档清晰度时选最高的。"),
            ("链接必须是新鲜的", "小红书的分享链接带一个几小时内有效的 xsec_token，过期就打不开。请在 App 里现复制现贴，不要用几天前保存的链接。xhslink.com 短链和网页版分享出来的长链接都可以。"),
            ("和 App 内保存的区别", "App 内长按保存的是压缩后的 1080 宽图片，实况图只剩静态一帧。通过链接解析拿到的是平台存储的原始尺寸，实况图连视频一起。"),
        ],
        common=["watermark", "iphone", "links"],
        guides=["xiaohongshu-live-photo-iphone", "xiaohongshu-link-expired", "video-to-live-photo"],
    ),
    Page(
        slug="kuaishou",
        title="快手视频去水印下载_图集提取 - 拾帧",
        description="粘贴快手分享链接，在线下载无水印视频和图集原图，可截一段转 GIF 或实况照片。免费不用登录。",
        h1="快手去水印，<br>视频图集直接存。",
        lead="快手 App 里点分享 → 复制链接，把整段文字贴进来。",
        keywords="快手去水印,快手视频下载,快手图集下载,快手无水印解析",
        placeholder="粘贴快手分享链接，如 https://v.kuaishou.com/xxxx",
        nav_label="快手去水印",
        faq=[
            ("快手长视频能解析吗？", "能。快手的短视频、长视频和图集用的是同一套分享链接，贴进来都能认，不用区分类型。"),
            ("为什么有的快手作品打不开？", "私密作品、已删除的作品和直播回放拿不到数据。另外快手的分享链接有时效，隔太久再用可能已经失效，回 App 重新复制一次就好。"),
        ],
        body=[
            ("快手链接能拿到什么", "视频作品：无水印 MP4，不带右下角的用户号水印。图集作品：每张图按原始尺寸列出来，可以逐张保存。解析完还能直接截一段做 GIF 或实况照片。"),
            ("怎么复制链接", "在快手 App 里点右侧的分享按钮 → 复制链接，会得到 v.kuaishou.com 开头的短链，整段文字贴进输入框即可，不用手动摘出网址。网页版 kuaishou.com 的长链接同样支持。"),
            ("和 App 内保存的区别", "快手 App 里保存的视频右下角带用户号水印，图集只能一张张长按。通过链接解析拿到的是无水印的原始文件，图集一次列全，逐张保存。"),
        ],
        common=["watermark", "links", "free"],
        guides=["video-to-gif", "video-to-live-photo"],
    ),
    Page(
        slug="youtube",
        title="YouTube 视频下载_1080p 4K_仅音频 - 拾帧",
        description="粘贴 YouTube 链接，在线下载 1080p、1440p、4K 视频或仅音频 m4a，还能截一段做 GIF 或实况照片。免费不用登录。",
        h1="<em>YouTube</em> 下载，<br>1080p 到 4K 都能选。",
        lead="把 YouTube 视频链接贴进来，清晰度列表里选一个，服务器合并音视频后给你一个完整的 MP4。",
        keywords="YouTube视频下载,油管下载,YouTube转MP4,YouTube转MP3,YouTube 1080p下载",
        placeholder="粘贴 YouTube 链接，如 https://youtu.be/xxxx",
        nav_label="YouTube 下载",
        faq=[
            ("YouTube 高清下载为什么要等一会儿？", "1080p 以上 YouTube 把画面和声音分开存，服务器要分别下载再合并，视频越长等得越久。360p / 720p 的合一版本可以直接播放和保存。"),
        ],
        body=[
            ("YouTube 链接能拿到什么", "360p 的合一版本可以直接播放和保存；720p、1080p、1440p、4K 由服务器下载并合并音视频后提供完整 MP4；「仅音频」给原始 m4a 音轨。清晰度菜单里会显示每档的预估大小。"),
            ("等待时间", "高清档位需要服务器先下载再合并，10 分钟的 1080p 视频通常在一分钟内完成，4K 长视频可能要几分钟。页面上有进度条，完成后点保存即可。"),
        ],
        common=["free", "ratelimit", "gifsize"],
        guides=["youtube-1080p-download", "video-to-gif"],
    ),
    Page(
        slug="x",
        title="X (Twitter) 视频下载_推文图片原图 - 拾帧",
        description="粘贴 X / Twitter 推文链接，在线下载推文里的视频（最高码率）和图片原图，可转 GIF 或实况照片。免费不用登录。",
        h1="<em>X</em> 推文里的视频与图片，<br>直接保存。",
        lead="复制推文链接（x.com 或 twitter.com 都行）贴进来。视频取最高码率的 MP4，图片是原图。",
        keywords="推特视频下载,Twitter视频下载,X视频下载,推特图片原图,twitter gif 下载",
        placeholder="粘贴推文链接，如 https://x.com/user/status/xxxx",
        nav_label="X 视频下载",
        faq=[
            ("推文里的 GIF 下载下来为什么是 MP4？", "X 在上传时就把 GIF 转成了循环播放的 MP4，平台本身不再保存 GIF 原文件，所以这里拿到的也是 MP4。确实需要 GIF 的话，解析后点\"转 GIF\"再转一次即可。"),
            ("需要登录才能看的推文能解析吗？", "能，而且不需要你登录 X。标记为敏感内容、或者提示\"登录后查看\"的推文会自动换一条通道取数据。已删除、账号已注销、设为仅关注者可见的推文拿不到。"),
        ],
        body=[
            ("推文能拿到什么", "视频：从所有码率版本里挑最高的那个 MP4。图片：多图推文把每张都列出来，按原图尺寸保存，不是时间线上压缩过的缩略图。GIF：以 MP4 形式给出，可以再转成真正的 GIF 文件。"),
            ("x.com 和 twitter.com 都认", "改名前后的两种域名都支持，App 分享出来的带一串参数的链接也能直接贴。复制地址栏里的推文链接，或者点推文下方的分享 → 复制链接，整段贴进来即可。"),
            ("常见用法", "把推文里的短视频做成表情包：解析后点\"转 GIF\"，拖出想要的几秒。保存多图推文的全部原图：解析后逐张保存。把一段视频做成 iPhone 实况照片：点\"做成实况照片\"，自己选段和封面帧。"),
        ],
        common=["watermark", "links", "free"],
        guides=["video-to-gif"],
    ),
    Page(
        slug="bilibili",
        title="B站视频下载_1080p 高清_仅音频 - 拾帧",
        description="粘贴 B站 视频链接，在线下载 1080p / 720p 视频或仅音频，可截一段做 GIF 或实况照片。b23.tv 短链也支持。免费不用登录。",
        h1="B站视频下载，<br>1080p 高清直接选。",
        lead="把 B站 链接（bilibili.com 或 b23.tv）贴进来，清晰度列表里选一档。",
        keywords="B站视频下载,bilibili下载,哔哩哔哩视频保存,B站1080p下载,B站音频提取",
        placeholder="粘贴 B站 链接，如 https://b23.tv/xxxx",
        nav_label="B站下载",
        faq=[
            ("为什么默认只给 480p？", "网页端接口直接返回的播放地址就是 480p 的合一版本，点开立刻能播能存。720p 和 1080p 在 B站 是画面与声音分开存的，要服务器分别下载再合并，所以单列在\"其他清晰度\"里，点了需要等一会儿。"),
            ("多 P 视频会解析哪一集？", "固定解析第一 P。链接里的 p= 参数目前不生效，所以合集、课程这类多 P 投稿只能拿到第 1 集。"),
        ],
        body=[
            ("B站链接能拿到什么", "默认给一个可直接播放的 480p MP4；720p、1080p 和仅音频由服务器下载合并后提供完整文件。BV 号完整链接、b23.tv 短链、手机 App 分享出来的链接都能直接贴。"),
            ("支持哪些链接", "只支持普通投稿视频，也就是网址里带 /video/BV… 的那种。番剧、影视、课堂和直播回放（/bangumi/、/cheese/ 等路径）不在支持范围内。多 P 投稿固定取第一 P。"),
            ("清晰度说明", "不登录的情况下最高到 1080p。4K、HDR、杜比这些档位属于大会员专属，网页端不登录拿不到，这里也就不提供。仅音频档给的是从视频里分离出来的原始音轨，适合存 BGM 或者课程录音。"),
        ],
        common=["free", "ratelimit", "links"],
        guides=["video-to-gif", "video-to-live-photo"],
    ),
    Page(
        slug="gif",
        title="视频转 GIF 在线工具_抖音小红书链接直接转 - 拾帧",
        description="在线把视频转成 GIF：粘贴抖音、小红书、YouTube 链接或上传本地视频，拖缩略图条选段，可调帧率、宽度、速度和抖动。免费不用登录。",
        h1="视频转 <em>GIF</em>，<br>选一段就好。",
        lead="贴一个视频链接，或者上传本地视频。在缩略图条上拖出要的那几秒，帧率、宽度、速度都能调。",
        keywords="视频转GIF,在线GIF制作,抖音视频转GIF,MP4转GIF,GIF压缩,GIF制作工具",
        nav_label="视频转 GIF",
        faq=[
            ("GIF 最长能做多长？", "30 秒。GIF 超过 10 秒体积会很大，一般 3–8 秒最好用。"),
            ("怎么让 GIF 更清晰或更小？", "清晰：把宽度拉大、抖动选\"误差扩散\"。小：降帧率到 8–10、宽度 320–360、时长缩短。"),
        ],
        body=[
            ("怎么用", "贴链接解析后点「转 GIF」，或者点右上角「本地视频」上传手机里的视频。在缩略图条上拖两个把手选段，右侧调帧率、宽度、速度，点生成，几秒后直接预览并保存。"),
            ("推荐参数", "微信表情包：240–320 px、8–10 fps、2–3 秒，体积 1 MB 以内。群聊里看字幕：360–480 px、12 fps。想保留细节：640 px 以上，但体积会很大。"),
        ],
        common=["gifsize", "ratelimit", "free"],
        guides=["video-to-gif", "douyin-no-watermark"],
    ),
    Page(
        slug="live-photo",
        title="视频转实况照片_Live Photo 动态照片在线制作 - 拾帧",
        description="在线把视频片段做成 iPhone 实况照片（配对好的 JPG + MOV）或安卓动态照片（内嵌视频的 JPG），支持抖音、小红书链接和本地视频，可选封面帧。",
        h1="视频做成 <em>Live Photo</em>，<br>iPhone 与安卓都行。",
        lead="贴链接或上传视频，选最多 10 秒，挑一帧做封面。iPhone 得到配对好的 JPG + MOV，安卓得到一张会动的 JPG。",
        keywords="视频转实况照片,视频转Live Photo,实况照片制作,动态照片制作,Motion Photo,小红书实况图,抖音实况图",
        nav_label="视频转实况",
        faq=[
            ("实况照片的封面能自己选吗？", "能。转换面板里拖\"封面帧\"，或者把预览播到想要的画面点\"用当前画面做封面\"。"),
            ("为什么 iPhone 不能直接保存实况？", "网页没有权限往 iOS 相册写入配对的实况。zip 里的 JPG 和 MOV 已经配好对，用支持合成实况的 App 或快捷指令导入即可，相册会显示为一张实况。"),
        ],
        body=[
            ("两种输出", "iPhone 实况照片：一对 JPG + MOV，写入相同的 Apple 内容标识，打包成 zip；导入相册后长按会动，可设为锁屏动态壁纸。安卓动态照片：一张内嵌 MP4 的 JPG（MVIMG_ 开头），存到相册即可，Google 相册、三星、小米、OPPO、vivo 都识别。"),
            ("从哪里来的视频都行", "抖音、小红书、YouTube 等链接解析后点「做成实况照片」；本地视频点右上角上传。小红书和抖音图集里自带的实况图不用裁剪，解析后直接「实况原样打包」。"),
        ],
        common=["iphone", "android", "free"],
        guides=["video-to-live-photo", "xiaohongshu-live-photo-iphone", "douyin-image-post-original"],
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
            "featureList": ["无水印视频提取", "原图提取", "视频转 GIF", "视频转实况照片", "动态照片"],
        },
        _faq_ld(page.all_faq),
    ]
    if not page.slug:
        data.append({"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
                     "url": absolute(base, "/"), "inLanguage": "zh-CN"})
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
            "datePublished": guide.updated,
            "dateModified": guide.updated,
            "author": {"@type": "Organization", "name": SITE_NAME, "url": absolute(base, "/")},
            "publisher": {"@type": "Organization", "name": SITE_NAME},
            "mainEntityOfPage": url,
        },
        _faq_ld(guide.faq),
        _crumbs(base, (SITE_NAME, "/"), ("教程", "/guides"), (guide.h1, guide.path)),
    ]
    return _dump(data)


def sitemap_xml(base: str) -> str:
    def url(path: str, priority: str, freq: str = "weekly") -> str:
        return (f"<url><loc>{absolute(base, path)}</loc><lastmod>{UPDATED}</lastmod>"
                f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>")

    items = [url(p.path, "1.0" if not p.slug else "0.8") for p in PAGES]
    items.append(url("/guides", "0.7"))
    items += [url(g.path, "0.7", "monthly") for g in GUIDES]
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(items) + "</urlset>")


def all_paths() -> list[str]:
    """给主动推送脚本用。"""
    return [p.path for p in PAGES] + ["/guides"] + [g.path for g in GUIDES]
