import dataclasses
import json
import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

import fake_useragent
import httpx

from ..utils import create_async_client
from .errors import ParseError


class VideoSource(Enum):
    """
    视频来源：douiyin，kuaishou...
    """

    DouYin = "douyin"  # 抖音 / 抖音火山版（原 火山小视频）
    KuaiShou = "kuaishou"  # 快手
    PiPiXia = "pipixia"  # 皮皮虾
    WeiBo = "weibo"  # 微博
    WeiShi = "weishi"  # 微视
    LvZhou = "lvzhou"  # 绿洲
    ZuiYou = "zuiyou"  # 最右
    QuanMin = "quanmin"  # 度小视(原 全民小视频)
    XiGua = "xigua"  # 西瓜
    LiShiPin = "lishipin"  # 梨视频
    PiPiGaoXiao = "pipigaoxiao"  # 皮皮搞笑
    HuYa = "huya"  # 虎牙
    AcFun = "acfun"  # A站
    DouPai = "doupai"  # 逗拍
    MeiPai = "meipai"  # 美拍
    QuanMinKGe = "quanminkge"  # 全民K歌
    SixRoom = "sixroom"  # 六间房
    XinPianChang = "xinpianchang"  # 新片场
    HaoKan = "haokan"  # 好看视频
    BiliBili = "bilibili"  # 哔哩哔哩
    RedBook = "redbook"  # 小红书
    Twitter = "twitter"  # Twitter/X
    QQVideo = "qqvideo"  # 腾讯视频
    Sohu = "sohu"  # 搜狐视频
    CCTV = "cctv"  # 央视网
    QQMusic = "qqmusic"  # QQ 音乐封面
    NetEaseMusic = "netease"  # 网易云音乐封面
    Steam = "steam"  # Steam 点数商店素材
    HoYoPlay = "hoyoplay"  # HoYoPlay 官方启动器壁纸
    WutheringWaves = "wutheringwaves"  # 鸣潮国际服启动器壁纸
    MiYouShe = "miyoushe"  # 米游社单篇正文图集
    BiliAssets = "bilibili-assets"  # B 站公开装扮素材
    Telegram = "telegram"  # 公开目录收录的 Telegram 贴纸
    Animation = "animation"
    SevenTV = "7tv"
    BetterTTV = "bttv"
    FrankerFaceZ = "ffz"
    Line = "line"
    NotoEmoji = "noto-emoji"
    Signal = "signal"
    Discord = "discord"  # Discord 官方公开目录中的贴纸
    WeChatChannels = "wechat-channels"  # 视频号匿名公开预览
    AppleMusic = "applemusic"  # Apple Music 公开封面
    Pixiv = "pixiv"
    PixivSketch = "pixiv-sketch"
    Goofish = "goofish"  # 闲鱼公开商品图片与视频
    YtDlp = "ytdlp"  # YouTube / TikTok / Instagram 等，交给 yt-dlp


@dataclasses.dataclass
class VideoAuthor:
    """
    视频作者信息
    """

    # 作者ID
    uid: str = ""

    # 作者昵称
    name: str = ""

    # 作者头像
    avatar: str = ""


@dataclasses.dataclass
class ImgInfo:
    """
    图集图片信息
    """

    # 图片url
    url: str = ""

    # livephoto 视频地址
    live_photo_url: str = ""


@dataclasses.dataclass
class FormatInfo:
    """同一视频的下载档位；url 为直链，format_spec 为服务端下载选择器。"""

    # 展示名, 如 1080p / 音频
    label: str = ""

    # yt-dlp 的 -f 表达式
    format_spec: str = ""

    # 文件扩展名
    ext: str = "mp4"

    # 视频高度, 音频为 0
    height: int = 0

    # 预估大小（字节）, 未知为 0
    filesize: int = 0

    # 直链（抖音 / 小红书这类平台直接给出多档地址时用）; 为空则表示需要 yt-dlp 按 format_spec 下载
    url: str = ""

    # 编码说明, 如 "H.265"
    codec: str = ""

    width: int = 0
    fps: float = 0
    bitrate: int = 0  # bit/s；不同编码的码率不代表相同的画质


@dataclasses.dataclass
class MediaAsset:
    """同一套装里的独立素材；不属于同一视频的清晰度，也不是原生实况配对。"""

    url: str
    label: str = ""
    kind: str = "image"  # image / animation / video / archive / file
    cover_url: str = ""
    ext: str = ""  # TGS 等原始格式不能按普通图片下载


@dataclasses.dataclass
class ContentEntry:
    """目录中的可选作品或外部引用，不是可直接下载的媒体。"""

    url: str
    title: str = ""
    cover_url: str = ""
    kind: str = "artwork"
    available: bool = True


@dataclasses.dataclass
class TextDocument:
    """纯文本正文；调用方不得作为 HTML 插入页面。"""

    title: str
    content: str


@dataclasses.dataclass
class VideoInfo:
    """
    视频信息
    """

    # 视频播放地址
    video_url: str

    # 视频封面地址
    cover_url: str

    # 视频标题
    title: str = ""

    # 音乐播放地址
    music_url: str = ""

    # 图集图片地址列表
    images: list[ImgInfo] = dataclasses.field(default_factory=list)

    # 视频作者信息
    author: VideoAuthor = dataclasses.field(default_factory=VideoAuthor)

    # ---- 以下为本项目扩展字段, 上游解析器不填也没关系 ----

    # 来源平台标识, 见 VideoSource
    source: str = ""

    # 原始页面地址（yt-dlp 站点服务端下载时需要）
    page_url: str = ""

    # 时长（秒）, 未知为 0
    duration: float = 0

    # 视频尺寸, 未知为 0
    width: int = 0
    height: int = 0

    # 需要服务端合并下载的清晰度选项
    formats: list[FormatInfo] = dataclasses.field(default_factory=list)

    # 直链需要附带的请求头（Referer / Cookie 等）
    video_headers: dict[str, str] = dataclasses.field(default_factory=dict)

    # 给用户看的一句说明：平台不给的那部分是什么、怎么办（页面上显示在结果标题下面）
    notice: str = ""

    assets: list[MediaAsset] = dataclasses.field(default_factory=list)
    entries: list[ContentEntry] = dataclasses.field(default_factory=list)
    documents: list[TextDocument] = dataclasses.field(default_factory=list)
    next_page_url: str = ""
    entry_total: int = 0
    animation_frames: list[dict] = dataclasses.field(default_factory=list)
    content_kind: str = "media"  # media / directory / document

    video_fps: float = 0
    video_bitrate: int = 0
    video_codec: str = ""
    video_ext: str = "mp4"


def json_in_html(html: str, pattern: str, what: str) -> Any:
    """页面里内嵌的 JSON（pattern 的第一个分组）；找不到就是页面结构变了。"""
    match = re.search(pattern, html, re.S)
    if not match or not match.group(1).strip():
        raise ParseError("parse", f"页面里没找到{what}")
    return json.loads(match.group(1).strip())


_ua_pools: dict[str, fake_useragent.UserAgent] = {}

# fake_useragent 2.x 的系统名区分大小写。写成 "android" / "windows" 不报错，
# 只是悄悄退回一个固定的 Windows Chrome UA（日志里一行 "suppressed with fallback"）——
# 西瓜走手机页、本意是安卓 UA，实际一直发的是电脑 UA
_OS_NAMES = {"ios": "iOS", "android": "Android", "windows": "Windows", "linux": "Linux", "mac os x": "Mac OS X"}


def _random_ua(os: str = "iOS") -> str:
    # UserAgent() 每构造一次要 40ms（读数据集），.random 取值只要 3ms。
    # 池子本身是只读的，每种系统进程内留一个就够。
    os = _OS_NAMES.get(os.lower(), os)
    pool = _ua_pools.get(os)
    if pool is None:
        pool = _ua_pools[os] = fake_useragent.UserAgent(os=os)
    return pool.random


class BaseParser(ABC):
    def ua(self, os: str = "iOS") -> str:
        # 一次解析要发好几个请求（短链跳转、接口、页面），每个都换 UA 的话，
        # 在平台看来就是同一个 IP 上一串对不上号的客户端——本来就是风控信号，
        # 也让 cookie 握手那类要求会话一致的流程不可能成立。
        # 解析器每次解析都是新实例，所以这里按实例缓存 = 每次解析换一个 UA。
        cache = self.__dict__.setdefault("_uas", {})
        if os not in cache:
            cache[os] = _random_ua(os)
        return cache[os]

    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua()}

    # ------------------------------------------------------------------ 请求的公用写法

    async def fetch(
        self, method: str, url: str, *, headers: dict[str, str] | None = None, follow_redirects: bool = False, **kw
    ) -> httpx.Response:
        """发一个请求并检查状态码（4xx / 5xx 抛 httpx.HTTPStatusError）。headers 不给就用默认的随机 UA。"""
        async with create_async_client(follow_redirects=follow_redirects) as client:
            response = await client.request(method, url, headers=headers or self.get_default_headers(), **kw)
        response.raise_for_status()
        return response

    async def get_json(self, url: str, **kw) -> Any:
        return (await self.fetch("GET", url, **kw)).json()

    async def post_json(self, url: str, **kw) -> Any:
        return (await self.fetch("POST", url, **kw)).json()

    async def get_text(self, url: str, **kw) -> str:
        return (await self.fetch("GET", url, **kw)).text

    async def redirect_target(self, url: str, headers: dict[str, str] | None = None) -> str:
        """短链跳到哪儿（不跟跳转，读 Location）；没跳转返回空串。"""
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(url, headers=headers or self.get_default_headers())
        return response.headers.get("location", "")

    @abstractmethod
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        """解析分享链接，获取视频信息"""

    @abstractmethod
    async def parse_video_id(self, video_id: str) -> VideoInfo:
        """按作品 ID 解析，获取视频信息"""
