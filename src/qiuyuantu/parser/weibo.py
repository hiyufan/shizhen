import json
import re
from urllib.parse import urlencode, urljoin, urlparse

import httpx

from ..utils import gather_or_cancel, get_val_from_url_by_query_key
from . import mp4_probe
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo, json_in_html
from .errors import ParseError
from .quality import apply_video_formats, direct_format, number

APP_SHARE_HOST = "mapp.api.weibo.cn"
LIVE_UNSUPPORTED = "微博直播和直播回放暂不支持下载"
# 图片按从大到小找第一个有地址的尺寸
_PIC_SIZES = ("original", "largest", "large", "bmiddle")


def _largest_pic(pic: dict) -> str:
    return next((pic[size]["url"] for size in _PIC_SIZES if (pic.get(size) or {}).get("url")), pic.get("url") or "")


def _url(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return "https:" + value if value.startswith("//") else value


def _status_formats(media: dict) -> list[FormatInfo]:
    formats = []
    for row in media.get("playback_list") or []:
        play = row.get("play_info") or {}
        if play.get("url") and not str(play.get("mime", "")).startswith("image/"):
            data = play | {"videoCodec": play.get("video_codecs") or ""}
            formats.append(direct_format(_url(play["url"]), data, label=play.get("quality_desc") or ""))
    if not formats:
        for key in ("mp4_1080p_mp4", "mp4_720p_mp4", "stream_url_hd", "stream_url"):
            if value := media.get(key):
                formats.append(direct_format(_url(value), {}))
    return formats


def _status_images(status: dict) -> list[ImgInfo]:
    pics = status.get("pics") or []
    if not pics:
        by_id = status.get("pic_infos") or {}
        pics = [by_id[key] for key in status.get("pic_ids") or by_id if key in by_id]
    return [ImgInfo(url=_url(url)) for pic in pics if (url := _largest_pic(pic))]


def _from_status(status: dict) -> VideoInfo:
    """普通微博也可以包含视频；不能把它们一律当作纯图集。"""
    user = status.get("user") or {}
    page = status.get("page_info") or {}
    # 直播卡片的 stream_url 是播放页而不是视频文件，回放只有分段直播流
    live = page.get("object_type") == "live"
    if live and not status.get("pics") and not status.get("pic_ids"):
        raise ParseError("unsupported", LIVE_UNSUPPORTED)
    media = {} if live else page.get("media_info") or {}
    info = VideoInfo(
        video_url="",
        cover_url=_url(page.get("page_pic") or media.get("poster") or ""),
        title=re.sub(r"<[^>]*>", "", status.get("text", "")).strip(),
        images=_status_images(status),
        author=VideoAuthor(name=user.get("screen_name", ""), avatar=user.get("avatar_large", "")),
        duration=number(media.get("duration")),
        video_headers={"Referer": "https://weibo.com/"},
    )
    return apply_video_formats(info, _status_formats(media))


async def _verified_formats(formats: list[FormatInfo]) -> list[FormatInfo]:
    if len(formats) > 32:
        raise ParseError("parse", "微博返回的清晰度档位数量异常")
    unknown = [fmt for fmt in formats if fmt.url and not fmt.height]
    probes = await gather_or_cancel(*(mp4_probe.probe(f.url, {"Referer": "https://weibo.com/"}) for f in unknown))
    verified = {old.url: new for old, new in zip(unknown, probes, strict=True) if new}
    return [verified.get(fmt.url) or fmt for fmt in formats]


async def _verify_video(info: VideoInfo) -> VideoInfo:
    formats = list(info.formats)
    if info.video_url:
        formats.insert(
            0,
            FormatInfo(
                url=info.video_url,
                width=info.width,
                height=info.height,
                fps=info.video_fps,
                bitrate=info.video_bitrate,
                codec=info.video_codec,
            ),
        )
    if not formats:
        return info
    info.video_url = ""
    return apply_video_formats(info, await _verified_formats(formats))


class WeiBo(BaseParser):
    """微博视频、普通微博中的视频与图集。"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        if urlparse(share_url).hostname == APP_SHARE_HOST:
            target = urljoin(share_url, await self.redirect_target(share_url))
            if urlparse(target).hostname in {None, APP_SHARE_HOST}:
                raise ParseError("unsupported", "这条微博分享链接没有跳到微博内容")
            return await self.parse_share_url(target)
        if "show?fid=" in share_url:
            return await self.parse_video_id(get_val_from_url_by_query_key(share_url, "fid"))
        path = urlparse(share_url).path
        if "/tv/show/" in path:
            return await self.parse_video_id(path.replace("/tv/show/", ""))
        if path.startswith("/l/wblive/"):
            raise ParseError("unsupported", LIVE_UNSUPPORTED)
        # t.cn 短链有时落到 weibo.com/p/230444<微博 mid>，是这条微博的卡片页
        if card := re.fullmatch(r"/p/230444([0-9]{16})/?", path):
            return await self.parse_post(card[1], share_url)
        # 普通微博：weibo.com/<用户 ID>/<微博 ID>；有的分享会在 ID 后面粘上别的字符
        parts = path.strip("/").split("/")
        post_id = re.match(r"[0-9A-Za-z]+", parts[-1]) if len(parts) >= 2 else None
        if post_id:
            return await self.parse_post(post_id[0], share_url)
        raise ParseError("unsupported", "不是微博视频或单条微博的链接")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        play = await self._play_info(video_id)
        if play.get("mid"):
            desktop = await self._desktop_status(str(play["mid"]))
            if desktop and (desktop.video_url or desktop.formats):
                return await _verify_video(desktop)
        formats = [direct_format(_url(url), {}, label=label) for label, url in (play.get("urls") or {}).items() if url]
        if not formats and play.get("stream_url"):
            formats = [direct_format(_url(play["stream_url"]), {})]
        info = VideoInfo(
            video_url="",
            cover_url=_url(play["cover_image"]),
            title=play["title"],
            author=VideoAuthor(uid=str(play["user"]["id"]), name=play["author"], avatar=_url(play["avatar"])),
        )
        return apply_video_formats(info, await _verified_formats(formats))

    async def _play_info(self, video_id: str) -> dict:
        payload = {"data": json.dumps({"Component_Play_Playinfo": {"oid": video_id}}, separators=(",", ":"))}
        try:
            body = await self._weibo_json(
                "https://weibo.com/tv/api/component?" + urlencode({"page": f"/tv/show/{video_id}"}),
                method="POST",
                data=payload,
            )
            play = (body.get("data") or {}).get("Component_Play_Playinfo")
            if isinstance(play, dict) and play:
                return play
        except (httpx.HTTPError, ValueError):
            pass
        body = await self.post_json(
            f"https://h5.video.weibo.com/api/component?page=/show/{video_id}",
            headers={"Referer": f"https://h5.video.weibo.com/show/{video_id}", "User-Agent": self.ua("iOS")},
            data=payload,
            follow_redirects=True,
        )
        play = (body.get("data") or {}).get("Component_Play_Playinfo")
        if not isinstance(play, dict) or not play:
            raise ParseError("restricted", "微博没有提供这条视频的数据")
        return play

    async def _weibo_json(self, url: str, *, method: str = "GET", **kwargs) -> dict:
        headers = {"User-Agent": self.ua("windows"), "Referer": "https://weibo.com/"}
        if cookie := self.__dict__.get("_guest_cookie"):
            headers["Cookie"] = cookie
        response = await self.fetch(method, url, headers=headers, follow_redirects=True, **kwargs)
        if response.url.host == "passport.weibo.com":
            headers["Cookie"] = await self._start_guest(str(response.url))
            response = await self.fetch(method, url, headers=headers, follow_redirects=True, **kwargs)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("微博没有返回有效的作品数据")
        return data

    async def _start_guest(self, visitor_url: str) -> str:
        """官方首次访问的匿名会话，只留在本次解析实例，不复用账号或跨用户 Cookie。"""
        headers = {"User-Agent": self.ua("windows"), "Referer": visitor_url}
        version = re.search(r"Chrome/([0-9]+)", headers["User-Agent"])
        fingerprint = {
            "os": "1",
            "browser": f"Chrome{version[1] if version else '90'},0,0,0",
            "fonts": "undefined",
            "screenInfo": "1920*1080*24",
            "plugins": "",
        }
        response = await self.fetch(
            "POST",
            "https://passport.weibo.com/visitor/genvisitor",
            headers=headers,
            data={"cb": "gen_callback", "fp": json.dumps(fingerprint, separators=(",", ":"))},
        )
        match = re.fullmatch(r"\s*(?:window\.gen_callback\s*&&\s*)?gen_callback\((.*)\);?\s*", response.text, re.S)
        if not match:
            raise ValueError("微博匿名访问初始化失败")
        data = json.loads(match[1]).get("data") or {}
        if not data.get("tid"):
            raise ValueError("微博没有提供匿名访问会话")
        response = await self.fetch(
            "GET",
            "https://passport.weibo.com/visitor/visitor",
            headers=headers,
            params={
                "a": "incarnate",
                "t": data["tid"],
                "w": 3 if data.get("new_tid") else 2,
                "c": f"{int(number(data.get('confidence')) or 100):03d}",
                "gc": "",
                "cb": "cross_domain",
                "from": "weibo",
                "_rand": "0.5",
            },
        )
        cookie = "; ".join(f"{key}={value}" for key, value in response.cookies.items() if key in {"SUB", "SUBP"})
        if not cookie:
            raise ValueError("微博匿名会话没有初始化成功")
        self._guest_cookie = cookie
        return cookie

    async def parse_post(self, post_id: str, page_url: str) -> VideoInfo:
        """桌面接口提供完整播放档位；手机接口与内嵌数据留作访问失败时的备用。"""
        desktop = await self._desktop_status(post_id, missing_is_final=True)
        if desktop and (desktop.video_url or desktop.formats or desktop.images):
            return await _verify_video(desktop)
        try:
            body = await self.get_json(
                f"https://m.weibo.cn/statuses/show?id={post_id}",
                headers={
                    "User-Agent": self.ua("iOS"),
                    "Referer": "https://m.weibo.cn/",
                    "Content-Type": "application/json;charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                },
                follow_redirects=True,
            )
        except (httpx.HTTPError, ValueError):
            body = {}
        if isinstance(body.get("data"), dict):
            return await _verify_video(_from_status(body["data"]))
        info = self._from_page(
            await self.get_text(page_url, headers={"User-Agent": self.ua("iOS")}, follow_redirects=True)
        )
        return await _verify_video(info)

    async def _desktop_status(self, post_id: str, *, missing_is_final: bool = False) -> VideoInfo | None:
        try:
            body = await self._weibo_json(f"https://weibo.com/ajax/statuses/show?id={post_id}")
        except (httpx.HTTPError, ValueError):
            return None
        if missing_is_final and isinstance(body, dict) and str(body.get("error_code")) == "20101":
            # 删除或不存在的微博；手机接口与页面同样拿不到，不再往下试。
            # 视频页的播放信息还在时仍可下载，所以只在按微博解析时这样处理。
            raise ParseError("deleted", "微博说这条微博不存在")
        if not isinstance(body, dict) or not any(key in body for key in ("page_info", "pic_infos", "pics")):
            return None
        return _from_status(body)

    @staticmethod
    def _from_page(html: str) -> VideoInfo:
        # 页面里是 var $render_data = [{"status": {...}}][0] || {};
        # 以前截出 [...] 之后又拼上 "[0]" 再 json.loads，必然报错，这条兜底从来没成功过
        data = json_in_html(html, r"\$render_data\s*=\s*(\[.*?\])\[0\]", "微博数据")
        if not data or not isinstance(data[0], dict):
            raise ParseError("parse", "微博页面里的数据是空的")
        return _from_status(data[0].get("status") or {})
