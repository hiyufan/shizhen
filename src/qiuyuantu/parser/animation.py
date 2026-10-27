"""公开特殊动画文件链接；实际格式和资源限制在转换时重新校验。"""

from urllib.parse import urlparse

from .base import BaseParser, MediaAsset, VideoInfo
from .errors import ParseError


def file_kind(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.port:
            return ""
    except ValueError:
        return ""
    path = parsed.path.lower()
    if path.endswith(".spine.zip"):
        return "spine"
    return next((kind for kind in ("svga", "pag", "vap") if path.endswith("." + kind)), "")


class Animation(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        kind = file_kind(share_url)
        if not kind:
            raise ParseError("unsupported", "请使用公开的 SVGA、PAG、VAP 或 .spine.zip 文件链接")
        extension = "zip" if kind == "spine" else kind
        return VideoInfo(
            video_url="",
            cover_url="",
            title=kind.upper(),
            page_url=share_url,
            assets=[MediaAsset(url=share_url, label=kind.upper(), kind="animation", ext=extension)],
            notice="原始文件可直接下载；转换时会重新检查格式。Spine 链接使用首个动画，上传 ZIP 可选择动画。",
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise ParseError("unsupported", "特殊动画需要完整的公开文件链接，或上传本地文件")
