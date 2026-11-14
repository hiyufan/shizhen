"""Threads 帖子：yt-dlp 没有 Threads 提取器，以前所有 Threads 链接都失败（issue #9）。

未登录用手机浏览器打开帖子页时，服务器直接把整条帖子写进页面内嵌的 JSON，字段和 Instagram 同一套
（image_versions2 / video_versions / carousel_media）；桌面浏览器拿到的只是要再跑脚本的空壳。
/share/ 分享短链同样只对手机浏览器跳到帖子页。页面里还有回复和推荐帖，按帖子 code 认出这一条。
外层只有文字的引用帖，媒体可能位于它明确关联的 quoted_attachment_post，不能当作空帖。
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from . import instagram
from .base import BaseParser, VideoInfo
from .errors import ParseError

HOSTS = {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}
ROOT = "https://www.threads.com"
_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
CODE = r"[A-Za-z0-9_-]{5,40}"
_POST = re.compile(rf"/(?:@[\w.]{{1,40}}/post|t)/({CODE})(?:/media)?/?")
_SHARE = re.compile(rf"/share/{CODE}/?")
_JSON_SCRIPT = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)
POST_HINT = "请打开 Threads 里的单条帖子，再复制分享链接"


def post_code(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname not in HOSTS:
        return ""
    match = _POST.fullmatch(parsed.path)
    return match[1] if match else ""


def find_post(html: str, code: str) -> dict | None:
    for blob in _JSON_SCRIPT.findall(html):
        if code not in blob:
            continue
        try:
            post = _post_in(json.loads(blob), code)
        except ValueError:
            continue
        if post:
            return post
    return None


def _post_in(obj, code: str) -> dict | None:
    if isinstance(obj, dict):
        if obj.get("code") == code and "media_type" in obj:
            return obj
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        if (found := _post_in(child, code)) is not None:
            return found
    return None


def _quoted_post(post: dict) -> dict | None:
    app_info = post.get("text_post_app_info")
    if not isinstance(app_info, dict):
        return None
    share = app_info.get("share_info")
    if not isinstance(share, dict):
        return None
    if share.get("quoted_attachment_post_unavailable"):
        raise ParseError("restricted", "此帖引用的原帖当前不可公开读取")
    quoted = share.get("quoted_attachment_post")
    return quoted if isinstance(quoted, dict) else None


def _media_info(post: dict, code: str) -> tuple[VideoInfo, dict]:
    # 只沿选中帖子的明确引用关系读取，不扫描回复、推荐或外链预览；最多经过三层引用。
    for depth in range(4):
        if info := instagram.to_video_info(post, code):
            if depth:
                info.notice = "以下媒体来自此帖引用的原帖，作者信息保留原作者。"
            return info, post
        quoted = _quoted_post(post)
        if quoted is None:
            break
        post = quoted
    else:
        raise ParseError("unsupported", "引用层级过多，请复制原帖链接")
    raise ParseError("empty", "这条 Threads 帖子没有图片或视频")


class Threads(BaseParser):
    def get_default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        code = post_code(share_url) or await self._shared_code(share_url)
        return await self.parse_video_id(code)

    async def _shared_code(self, share_url: str) -> str:
        parsed = urlparse(share_url)
        if parsed.hostname not in HOSTS or not _SHARE.fullmatch(parsed.path):
            raise ParseError("unsupported", POST_HINT)
        code = post_code(urljoin(share_url, await self.redirect_target(share_url)))
        if not code:
            raise ParseError("unsupported", "这个 Threads 分享链接没有跳到帖子")
        return code

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(CODE, video_id):
            raise ParseError("unsupported", POST_HINT)
        # /t/<code> 会跳到带用户名的正式地址
        html = await self.get_text(f"{ROOT}/t/{video_id}", follow_redirects=True)
        post = find_post(html, video_id)
        if not post:
            raise ParseError("restricted", "Threads 没有公开这条帖子，可能已删除或仅部分人可见")
        info, media_post = _media_info(post, video_id)
        user = media_post.get("user") or {}
        info.source = "threads"
        info.author.avatar = str(user.get("profile_pic_url") or "")
        # 来源页保留用户分享的外层帖子；作者与标题来自实际供图 / 供视频的帖子。
        username = str((post.get("user") or {}).get("username") or "")
        if not re.fullmatch(r"[\w.]{1,40}", username):
            username = "_"
        info.page_url = f"{ROOT}/@{username}/post/{video_id}"
        return info
