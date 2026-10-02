import base64

from parsel import Selector

from .base import BaseParser, VideoAuthor, VideoInfo


def _cut(s: str, start: int, length: int) -> str:
    """去掉 s[start:start+length]，并把后面再出现的同样一段也删掉"""
    piece = s[start : start + length]
    return s[:start] + s[start + length :].replace(piece, "")


def decode_video_url(encoded: str) -> str:
    """美拍页面上的视频地址是混淆过的 base64：前 4 位倒过来是个十六进制数，
    它的十进制各位数字给出两次「从哪儿删多长」，删完剩下的才是真正的 base64。"""
    digits = [int(d) for d in str(int(encoded[:4][::-1], 16))]
    head, tail = digits[:-2], digits[-2:]
    body = _cut(encoded[4:], head[0], head[1])
    body = _cut(body, len(body) - tail[0] - tail[1], tail[1])
    return "https:" + base64.b64decode(body).decode("utf-8")


class MeiPai(BaseParser):
    """美拍"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        sel = Selector(await self.get_text(share_url, headers={"User-Agent": self.ua("windows")}))
        return VideoInfo(
            video_url=decode_video_url(sel.css("#shareMediaBtn::attr(data-video)").get(default="")),
            cover_url=sel.css("#detailVideo img::attr(src)").get(default=""),
            title=sel.css(".detail-cover-title::text").get(default="").strip(),
            author=VideoAuthor(
                uid=sel.css(".detail-name a::attr(href)").get(default="").split("/")[-1],
                name=sel.css(".detail-avatar::attr(alt)").get(default=""),
                avatar="https:" + sel.css(".detail-avatar::attr(src)").get(default=""),
            ),
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(f"https://www.meipai.com/video/{video_id}")
