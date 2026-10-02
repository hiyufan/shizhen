import json

from parsel import Selector

from .base import BaseParser, VideoAuthor, VideoInfo
from .errors import ParseError


class XinPianChang(BaseParser):
    """新片场"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        headers = {
            "User-Agent": self.ua("windows"),
            "Upgrade-Insecure-Requests": "1",
            "Referer": "https://www.xinpianchang.com/",
        }
        html = await self.get_text(share_url, headers=headers, follow_redirects=True)
        next_data = Selector(html).css("script#__NEXT_DATA__::text").get()
        if not next_data:
            raise ParseError("parse", "页面里没找到作品数据")
        detail = json.loads(next_data)["props"]["pageProps"]["detail"]
        # 页面里只有 appKey 和 media_id，mp4 地址要另调一个接口
        media = await self.get_json(
            f"https://mod-api.xinpianchang.com/mod/api/v2/media/{detail['media_id']}"
            f"?appKey={detail['video']['appKey']}&extend=userInfo%2CuserStatus",
            headers=headers,
            follow_redirects=True,
        )
        user = detail["author"]["userinfo"]
        return VideoInfo(
            video_url=media["data"]["resource"]["progressive"][0]["url"],
            cover_url=detail["cover"],
            title=detail["title"],
            author=VideoAuthor(uid=str(user["id"]), name=user["username"], avatar=user["avatar"]),
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise NotImplementedError("新片场暂不支持直接解析视频ID")
