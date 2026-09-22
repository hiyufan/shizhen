import dataclasses
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List

import fake_useragent


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
    """
    需要服务端合并下载的清晰度选项（yt-dlp 站点用）
    """

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
    images: List[ImgInfo] = dataclasses.field(default_factory=list)

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
    formats: List[FormatInfo] = dataclasses.field(default_factory=list)

    # 直链需要附带的请求头（Referer / Cookie 等）
    video_headers: Dict[str, str] = dataclasses.field(default_factory=dict)


_ua_pool: fake_useragent.UserAgent | None = None


def _random_ua() -> str:
    # UserAgent() 每构造一次要 40ms（读数据集），.random 取值只要 3ms。
    # 池子本身是只读的，进程内留一个就够。
    global _ua_pool
    if _ua_pool is None:
        _ua_pool = fake_useragent.UserAgent(os="iOS")
    return _ua_pool.random


class BaseParser(ABC):
    _ua: str = ""

    def get_default_headers(self) -> Dict[str, str]:
        # 一次解析要发好几个请求（短链跳转、接口、页面），每个都换 UA 的话，
        # 在平台看来就是同一个 IP 上一串对不上号的客户端——本来就是风控信号，
        # 也让 cookie 握手那类要求会话一致的流程不可能成立。
        # 解析器每次解析都是新实例，所以这里按实例缓存 = 每次解析换一个 UA。
        if not self._ua:
            self._ua = _random_ua()
        return {"User-Agent": self._ua}

    @abstractmethod
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        """
        解析分享链接, 获取视频信息
        :param share_url: 视频分享链接
        :return: VideoInfo
        """
        pass

    @abstractmethod
    async def parse_video_id(self, video_id: str) -> VideoInfo:
        """
        解析视频ID, 获取视频信息
        :param video_id: 视频ID
        :return:
        """
        pass
