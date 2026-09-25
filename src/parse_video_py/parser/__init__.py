from .acfun import AcFun
import asyncio
import logging

from ..utils import current_source
from .base import FormatInfo, ImgInfo, VideoAuthor, VideoInfo, VideoSource
from .errors import ParseError, classify
from .bilibili import BiliBili
from .cctv import CCTV
from .doupai import DouPai
from .douyin import DouYin
from .haokan import HaoKan
from .huya import HuYa
from .kuaishou import KuaiShou
from .lishipin import LiShiPin
from .lvzhou import LvZhou
from .meipai import MeiPai
from .pipigaoxiao import PiPiGaoXiao
from .pipixia import PiPiXia
from .qqvideo import QQVideo
from .quanmin import QuanMin
from .quanminkge import QuanMinKGe
from .redbook import RedBook
from .sixroom import SixRoom
from .sohu import Sohu
from .twitter import Twitter
from .weibo import WeiBo
from .weishi import WeiShi
from .xigua import XiGua
from .xinpianchang import XinPianChang
from .ytdlp import YtDlp
from .zuiyou import ZuiYou

logger = logging.getLogger(__name__)

# 视频来源与解析器的映射关系
video_source_info_mapping = {
    VideoSource.AcFun: {
        "domain_list": ["www.acfun.cn"],
        "parser": AcFun,
    },
    VideoSource.CCTV: {
        "domain_list": ["tv.cctv.cn", "tv.cctv.com"],
        "parser": CCTV,
    },
    VideoSource.DouPai: {
        "domain_list": ["doupai.cc"],
        "parser": DouPai,
    },
    VideoSource.DouYin: {
        "domain_list": ["v.douyin.com", "www.iesdouyin.com", "www.douyin.com"],
        "parser": DouYin,
    },
    VideoSource.HaoKan: {
        "domain_list": [
            "haokan.baidu.com",
            "haokan.hao123.com",
        ],
        "parser": HaoKan,
    },
    VideoSource.BiliBili: {
        "domain_list": [
            "www.bilibili.com",
            "b23.tv",
            "m.bilibili.com",
        ],
        "parser": BiliBili,
    },
    VideoSource.HuYa: {
        "domain_list": ["v.huya.com"],
        "parser": HuYa,
    },
    VideoSource.KuaiShou: {
        "domain_list": ["v.kuaishou.com"],
        "parser": KuaiShou,
    },
    VideoSource.LiShiPin: {
        "domain_list": ["www.pearvideo.com"],
        "parser": LiShiPin,
    },
    VideoSource.LvZhou: {
        "domain_list": ["weibo.cn"],
        "parser": LvZhou,
    },
    VideoSource.MeiPai: {
        "domain_list": ["meipai.com"],
        "parser": MeiPai,
    },
    VideoSource.PiPiGaoXiao: {
        "domain_list": ["h5.pipigx.com"],
        "parser": PiPiGaoXiao,
    },
    VideoSource.PiPiXia: {
        "domain_list": ["h5.pipix.com"],
        "parser": PiPiXia,
    },
    VideoSource.QuanMin: {
        "domain_list": ["xspshare.baidu.com"],
        "parser": QuanMin,
    },
    VideoSource.QuanMinKGe: {
        "domain_list": ["kg.qq.com"],
        "parser": QuanMinKGe,
    },
    VideoSource.SixRoom: {
        "domain_list": ["6.cn"],
        "parser": SixRoom,
    },
    VideoSource.Sohu: {
        "domain_list": ["tv.sohu.com", "my.tv.sohu.com"],
        "parser": Sohu,
    },
    VideoSource.WeiBo: {
        "domain_list": ["weibo.com"],
        "parser": WeiBo,
    },
    VideoSource.WeiShi: {
        "domain_list": ["isee.weishi.qq.com"],
        "parser": WeiShi,
    },
    VideoSource.XiGua: {
        # m.ixigua.com/video/<id> 是手机网页版链接，以前没列进来，会被当成未知站点交给 yt-dlp
        "domain_list": ["v.ixigua.com", "www.ixigua.com", "m.ixigua.com"],
        "parser": XiGua,
    },
    VideoSource.XinPianChang: {
        "domain_list": ["xinpianchang.com"],
        "parser": XinPianChang,
    },
    VideoSource.ZuiYou: {
        "domain_list": ["share.xiaochuankeji.cn"],
        "parser": ZuiYou,
    },
    VideoSource.RedBook: {
        "domain_list": [
            "www.xiaohongshu.com",
            "xhslink.com",
            "xhslink.cn",
        ],
        "parser": RedBook,
    },
    VideoSource.Twitter: {
        "domain_list": [
            "twitter.com",
            "x.com",
            "t.co",
            "mobile.twitter.com",
        ],
        "parser": Twitter,
    },
    VideoSource.QQVideo: {
        "domain_list": ["v.qq.com", "m.v.qq.com"],
        "parser": QQVideo,
    },
    VideoSource.YtDlp: {
        "domain_list": [
            "youtube.com",
            "youtu.be",
            "tiktok.com",
            "instagram.com",
            "vimeo.com",
            "facebook.com",
            "fb.watch",
            "twitch.tv",
            "reddit.com",
            "pinterest.com",
            "pin.it",
            "dailymotion.com",
            "threads.net",
            "threads.com",
        ],
        "parser": YtDlp,
    },
}


# 这些站点原生解析失败时退回 yt-dlp（B 站开始强制 WBI 签名、海外出口 412 之类）。
# 以前是每次都和 yt-dlp 同时跑、等它列出高清档位，整次解析被拖到 ~1100ms；
# 现在原生解析器自己从 DASH 接口拿档位，yt-dlp 只在失败时才上。
_FALLBACK_TO_YTDLP = {VideoSource.BiliBili}


async def _ytdlp_extra(share_url: str):
    # 走边缘中继又没有真代理时, yt-dlp 只能从服务器自己的出口访问, 海外机房必 412, 别白等几秒
    from ..convert import relay
    from ..utils import proxy_for

    if relay.enabled() and not proxy_for():
        return None
    try:
        return await asyncio.wait_for(YtDlp().parse_share_url(share_url), 25)
    except Exception:
        return None


def detect_source(share_url: str) -> VideoSource:
    """链接属于哪个平台；没有专用解析器的站点统统交给 yt-dlp 兜底。"""
    for item_source, item_source_info in video_source_info_mapping.items():
        for item_url_domain in item_source_info["domain_list"]:
            if item_url_domain in share_url:
                return item_source
    return VideoSource.YtDlp


async def parse_video_share_url(share_url: str) -> VideoInfo:
    """
    解析分享链接, 获取视频信息; 失败时抛 ParseError (带 reason)
    :param share_url: 视频分享链接
    :return:
    """
    source = detect_source(share_url)
    url_parser = video_source_info_mapping[source]["parser"]
    if not url_parser:
        raise ValueError(f"source {source} has no video parser")

    _obj = url_parser()
    token = current_source.set(source.value)
    try:
        if source in _FALLBACK_TO_YTDLP:
            try:
                video_info = await _obj.parse_share_url(share_url)
            except Exception as exc:  # noqa: BLE001
                # 内容删了 / 链接不对：换 yt-dlp 也是一样的结果，别让用户多等几秒
                if classify(exc).reason in ("deleted", "unsupported"):
                    raise
                extra = await _ytdlp_extra(share_url)
                if extra is None or not (extra.formats or extra.video_url):
                    raise
                video_info = extra
                video_info.source = source.value
        else:
            video_info = await _obj.parse_share_url(share_url)
    except Exception as exc:  # noqa: BLE001 - 统一归类
        err = classify(exc)
        if err.reason == "parse" and not isinstance(exc, ParseError):
            # 归不了类才说"页面结构变了"。这是唯一需要站长看一眼的分支，
            # 不留现场的话事后只能靠复现去猜（而链接可能已经失效）。
            logger.warning("解析失败无法归类 source=%s url=%s", source.value, share_url, exc_info=exc)
        raise err from exc
    finally:
        current_source.reset(token)
    if not video_info.source:
        video_info.source = source.value
    if not video_info.page_url:
        video_info.page_url = share_url
    if not video_info.video_url and not video_info.images and not video_info.music_url and not video_info.formats:
        raise ParseError("empty")

    return video_info


async def parse_video_id(source: VideoSource, video_id: str) -> VideoInfo:
    """
    解析视频ID, 获取视频信息
    :param source: 视频来源
    :param video_id: 视频id
    :return:
    """
    if not video_id or not source:
        raise ValueError("video_id or source is empty")

    id_parser = video_source_info_mapping[source]["parser"]
    if not id_parser:
        raise ValueError(f"source {source} has no video parser")

    _obj = id_parser()
    video_info = await _obj.parse_video_id(video_id)

    return video_info
