"""从完整公开分享链接读取 Signal 包清单，媒体解密通过临时引用按需执行。"""

from ..convert import signal
from ..protobuf import fields
from .base import BaseParser, MediaAsset, VideoAuthor, VideoInfo
from .errors import ParseError


def _manifest(data: bytes) -> tuple[str, str, list[tuple[int, str]]]:
    title = author = ""
    stickers = []
    for number, value in fields(data, limit=250):
        if number in {1, 2} and isinstance(value, bytes):
            text = value.decode("utf-8")[:300]
            if number == 1:
                title = text
            else:
                author = text
        elif number == 4 and isinstance(value, bytes):
            row = dict(fields(value, limit=10))
            ident = row.get(1, 0)
            if not isinstance(ident, int) or not 0 <= ident <= 100_000:
                raise ValueError("Signal 贴纸编号无效")
            emoji = row.get(2, b"")
            stickers.append((ident, emoji.decode("utf-8")[:64] if isinstance(emoji, bytes) else ""))
    if not 1 <= len(stickers) <= 200 or len({row[0] for row in stickers}) != len(stickers):
        raise ValueError("Signal 贴纸包为空、重复或超过数量限制")
    return title, author, stickers


class Signal(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        try:
            pack, key = signal.reference(share_url)
            raw = await signal.fetch_encrypted(pack, "manifest.proto", 1 << 20)
            title, author, rows = _manifest(signal.decrypt(raw, key))
        except ValueError as exc:
            raise ParseError("parse", str(exc)) from exc
        assets = []
        for ident, emoji in rows:
            url = signal.register(pack, key, ident)
            # 清单不提供文件格式，真实图片和帧数在取文件时校验；动画转换按需判断。
            assets.append(MediaAsset(url=url, label=emoji or str(ident), kind="animation", cover_url=url))
        return VideoInfo(
            video_url="",
            cover_url=assets[0].url,
            title=title or "Signal",
            assets=assets,
            author=VideoAuthor(name=author),
            page_url=signal.public_url(share_url),
            notice="素材来自持有分享链接即可读取的公开贴纸包；静态贴纸可直接下载，动态贴纸可继续转换。",
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise ParseError("unsupported", "Signal 需要包含包参数的完整分享链接")
