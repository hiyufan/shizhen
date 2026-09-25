from urllib.parse import urlparse

from ..utils import create_async_client
from .base import BaseParser, VideoInfo
from .douyin import DouYin
from .errors import ParseError


def _video_id_from(url: str) -> str:
    """路径里从后往前第一个纯数字段就是作品 ID。

    www.ixigua.com/<id>、m.ixigua.com/video/<id>/、www.iesdouyin.com/xg/video/<id>/
    都是这个形状；短链跳到首页 / 活动页这类地址时返回空，别拿别的段瞎猜。
    """
    for part in reversed(urlparse(url).path.strip("/").split("/")):
        if part.isdigit():
            return part
    return ""


class XiGua(BaseParser):
    """
    西瓜视频

    西瓜已并入抖音：作品 ID 就是抖音的 aweme_id，官方分享链接也换成了
    www.iesdouyin.com/xg/video/<id>。原来用的 m.ixigua.com/douyin/share/video 分享页
    其实就是抖音分享页换了个域名，2026-09 实测它和抖音一样不再在 SSR 里渲染
    videoInfoRes，所有作品都报 KeyError。数据直接走抖音解析器的 slidesinfo 接口。
    """

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        video_id = _video_id_from(share_url)
        if not video_id and urlparse(share_url).hostname == "v.ixigua.com":
            # 短链：不跟跳转，从 Location 里取 ID
            async with create_async_client(follow_redirects=False) as client:
                response = await client.get(share_url, headers={"User-Agent": self.ua("android")})
            video_id = _video_id_from(response.headers.get("location", ""))
            if not video_id:
                raise ParseError("deleted", "西瓜短链没有跳转到作品页")
        if not video_id:
            raise ParseError("unsupported", "链接里没有西瓜视频的作品 ID")
        return await self.parse_video_id(video_id)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await DouYin().parse_video_id(video_id)
