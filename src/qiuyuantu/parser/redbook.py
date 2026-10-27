import json
import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

# 原图: ci.xiaohongshu.com 上按 key 取, w/0 表示不缩放; 页面里给的都是缩到 1080 宽的版本
_ORIGINAL_IMAGE = "https://ci.xiaohongshu.com/{key}?imageView2/2/w/0/format/jpg/q/90"

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>", re.DOTALL)
_NOTE_PATH_RE = re.compile(r"/(?:explore|discovery/item|search_result)/([a-f0-9]{24})/?$", re.I)
_NOTE_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com", "rednote.com", "www.rednote.com"}

log = logging.getLogger("uvicorn.error")


class RedBook(BaseParser):
    """
    小红书

    桌面页提供较全的视频档位，手机分享页提供 noteData.data.noteData。
    匿名笔记长链优先使用官方 rednote 手机页；部分 pc_user 链接在国内域名
    返回安全跳转，在该入口仍可读取。保留原始参数，并核对返回的笔记 ID。
    """

    _prefer_mobile = False  # 桌面页撞过一次登录墙后就先走手机页

    def __init__(self) -> None:
        self._last_page = ("", "")  # 最近一次请求跳到的地址和页面标题, 失败时写进日志
        self._resolved_note_url = ""
        self._international_attempted = False

    @staticmethod
    def _mobile_first() -> bool:
        """中继匿名访问优先手机页，避免先请求通常需要登录的桌面入口。"""
        from ..convert import relay

        return relay.enabled() and not os.getenv("PARSE_VIDEO_XHS_COOKIE")

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        self._last_page = ("", "")
        self._resolved_note_url = ""
        self._international_attempted = False
        mobile_first = bool(_international_note_url(share_url)) or RedBook._prefer_mobile or self._mobile_first()
        queue = ["mobile", "desktop"] if mobile_first else ["desktop", "mobile"]
        expired: _Expired | None = None
        last_error: Exception | None = None
        while queue:
            mode = queue.pop(0)
            if expired or last_error:
                from ..progress import emit

                emit("fallback", fallback=True)
            try:
                info = await (self._parse_mobile if mode == "mobile" else self._parse_desktop)(share_url)
            except (_SecurityLimit, _WrongNote) as e:
                self._log_failure(mode, e)
                raise  # 明确受限或返回其他笔记时立即停止，避免继续请求或误取推荐内容。
            except _LoginWall as e:
                RedBook._prefer_mobile = True
                last_error = last_error or e  # 手机页先失败时, 它的原因比登录墙有用
            except _Expired as e:
                self._log_failure(mode, e)
                # 404 也可能是暂时返回，队列最多换路确认一次。
                expired = expired or e
            except Exception as e:  # noqa: BLE001 - 换一条路再试
                self._log_failure(mode, e)
                last_error = e
            else:
                if expired or last_error:
                    log.info("小红书%s页重试后解析成功 url=%s", _MODE_NAME[mode], share_url)
                return info
            if mode == "mobile" and queue == ["desktop"] and (RedBook._prefer_mobile or mobile_first):
                # 这个出口上桌面页只会是登录墙, 白跑一趟不如把手机页再请求一次
                queue = ["mobile"]
        raise (last_error if isinstance(last_error, ParseError) else expired or last_error) or ValueError(
            "小红书解析失败"
        )

    def _log_failure(self, mode: str, err: Exception) -> None:
        final_url, title = self._last_page
        log.warning(
            "小红书%s页没拿到笔记 %s: %s 跳到 %s 标题 %r",
            _MODE_NAME[mode],
            type(err).__name__,
            str(err)[:80],
            final_url[:200] or "-",
            title,
        )

    # ------------------------------------------------------------------ 桌面页
    async def _parse_desktop(self, share_url: str) -> VideoInfo:
        headers = {
            "User-Agent": _DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        # 保留配置过的国内桌面会话，不向国际域名发送它的 Cookie。
        if (cookie := os.getenv("PARSE_VIDEO_XHS_COOKIE")) and not _is_rednote(share_url):
            headers["Cookie"] = cookie
        final_url, html = await self._get(share_url, headers)
        json_data = self._state(final_url, html)
        if "note" not in json_data:
            raise self._block_error(final_url, html)
        note = json_data["note"]
        note_id = note.get("currentNoteId")
        data = ((note.get("noteDetailMap") or {}).get(note_id) or {}).get("note")
        if not data and (code := _security_limit(final_url, html)):
            raise _SecurityLimit(code)
        # 验证返回：小红书的分享链接有有效期，过期后会返回 undefined
        if not note_id or note_id == "undefined":
            raise _Expired("parse fail: note id in response is undefined")
        if not data:
            raise _Expired("parse fail: note detail is empty (链接可能过期或需要 xsec_token)")
        return self._build(data, image_url_key="urlDefault", nick_key="nickname")

    # ------------------------------------------------------------------ 手机分享页
    async def _parse_mobile(self, share_url: str) -> VideoInfo:
        target = share_url
        if not self._international_attempted and (
            alternate := _international_note_url(self._resolved_note_url or share_url)
        ):
            target = alternate
            self._international_attempted = True
        headers = {
            "User-Agent": _MOBILE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if (cookie := os.getenv("PARSE_VIDEO_XHS_COOKIE")) and not _is_rednote(target):
            headers["Cookie"] = cookie
        final_url, html = await self._get(target, headers)
        json_data = self._state(final_url, html)
        data = ((json_data.get("noteData") or {}).get("data") or {}).get("noteData")
        if not data:
            raise self._block_error(final_url, html)
        expected = _note_id(target) or _note_id(self._resolved_note_url)
        if expected and data.get("noteId") != expected:
            raise _WrongNote()
        info = self._build(data, image_url_key="url", nick_key="nickName")
        info.page_url = final_url
        return info

    # ------------------------------------------------------------------ 公共
    async def _get(self, url: str, headers: dict) -> tuple[str, str]:
        async with create_async_client(follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        final_url = str(response.url)
        for page in [*getattr(response, "history", ()), response]:
            note_id = _note_id(str(page.url))
            expected = _note_id(self._resolved_note_url)
            if note_id and (not expected or note_id == expected):
                self._resolved_note_url = str(page.url)
        self._last_page = (final_url, _title(response.text))
        return final_url, response.text

    def _state(self, final_url: str, html: str) -> dict:
        m = _STATE_RE.search(html)
        if not m or not m.group(1):
            raise self._block_error(final_url, html)
        # 页面里的 JSON 混着 JS 的 undefined, 换成 null 再用 json 解析 (比 yaml 快一个量级)
        return json.loads(re.sub(r"\bundefined\b", "null", m.group(1).strip()))

    @staticmethod
    def _image_key(page_url: str) -> str:
        """从页面图片地址取出存储 key，可能带 notes_pre_post/ 或 spectrum/ 前缀。

        http://sns-webpic-qc.xhscdn.com/<日期>/<hash>/notes_pre_post/<token>!h5_1080jpg -> notes_pre_post/<token>
        http://sns-webpic-qc.xhscdn.com/<日期>/<hash>/<token>!nd_dft_wlteh_jpg_3        -> <token>
        """
        parts = [p for p in urlparse(page_url).path.split("/") if p]
        if len(parts) < 3:
            return ""
        return "/".join(parts[2:]).split("!")[0]

    def _build(self, data: dict, *, image_url_key: str, nick_key: str) -> VideoInfo:
        """桌面页和手机页的笔记数据结构一样，只有图片地址和昵称的字段名不同。"""
        video = _video_tracks(data.get("video") or {})
        image_list = data.get("imageList") or []
        images = [] if video.url else [img for item in image_list if (img := self._image(item, image_url_key))]
        user = data.get("user") or {}
        return VideoInfo(
            video_url=video.url,
            cover_url=_page_image(image_list[0], image_url_key) if image_list else "",
            title=data.get("title") or (data.get("desc") or "")[:60],
            images=images,
            duration=video.duration,
            width=video.width,
            height=video.height,
            formats=video.formats,
            author=VideoAuthor(
                uid=user.get("userId", ""),
                name=user.get(nick_key) or user.get("nickname") or user.get("nickName") or "",
                avatar=user.get("avatar", ""),
            ),
        )

    def _image(self, item: dict, image_url_key: str) -> ImgInfo | None:
        """图集里的一张：换成原图分辨率的地址；实况图带上那段短视频。"""
        page_img = _page_image(item, image_url_key)
        if not page_img:
            return None
        key = item.get("fileId") or self._image_key(page_img)
        img = ImgInfo(url=_ORIGINAL_IMAGE.format(key=key) if key else page_img)
        live = _with_url((item.get("stream") or {}).get("h264"))
        if item.get("livePhoto") and live:
            img.live_photo_url = live[0]["masterUrl"]
        return img

    @staticmethod
    def _block_error(final_url: str, html: str) -> Exception:
        """页面不是笔记时, 说清楚小红书到底返回了什么, 站长才知道该配 cookie 还是代理。"""
        title_text = _title(html)
        path = urlparse(final_url).path
        if code := _security_limit(final_url, html):
            return _SecurityLimit(code)
        if path.startswith("/404/sec_"):
            # 相同跳转既可能是无效链接，也可能是 300013 安全拦截，不能据此断言作品已删除。
            return ParseError("restricted", "小红书返回安全跳转，无法确认笔记已删除或链接过期")
        if _is_404(final_url) or "页面不见了" in title_text:
            return _Expired("链接过期或笔记不存在 (小红书跳到了 404)")
        if path == "/login" or path.startswith(("/login/", "/website-login/")):
            return _LoginWall("小红书要求这个出口登录")
        markers = ("验证", "captcha", "verify", "安全", "网络连接异常", "海外")
        if any(m in html[:20000] or m in title_text for m in markers):
            return ValueError(
                "小红书对服务器所在网络返回了验证页 (被限流)，海外服务器请配置 "
                "PARSE_VIDEO_PROXY_CN / PARSE_VIDEO_RELAY_CN 或 PARSE_VIDEO_XHS_COOKIE"
            )
        return ValueError(f"小红书返回了意外页面 (标题: {title_text or '无'}，地址: {final_url[:80]})")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise NotImplementedError("小红书暂不支持直接解析视频ID")


_MODE_NAME = {"mobile": "手机", "desktop": "桌面"}


def _note_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname not in _NOTE_HOSTS or parsed.username or parsed.password:
        return ""
    match = _NOTE_PATH_RE.fullmatch(parsed.path)
    return match[1].lower() if match else ""


def _is_rednote(url: str) -> bool:
    return urlparse(url).hostname in {"rednote.com", "www.rednote.com"}


def _international_note_url(url: str) -> str:
    if not _note_id(url) or (os.getenv("PARSE_VIDEO_XHS_COOKIE") and not _is_rednote(url)):
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in _NOTE_HOSTS:
        return ""
    # 只替换官方域名，不重新编码 xsec_token，也不携带国内域名的登录 Cookie。
    return parsed._replace(netloc="www.rednote.com").geturl()


def _security_limit(final_url: str, html: str) -> str:
    query = parse_qs(urlparse(final_url).query)
    for code in ("300012", "300013"):
        if any(query.get(key) == [code] for key in ("code", "error_code", "errorCode")):
            return code
    if any(marker in html for marker in ("访问频繁", "安全限制", "IP存在风险")) and (
        match := re.search(r"(?<!\d)(300012|300013)(?!\d)", html)
    ):
        return match[1]
    return ""


def _is_404(final_url: str) -> bool:
    """是否为 404 路由；调用方需先区分 sec 安全跳转，参数中含 token 不代表 404。"""
    path = urlparse(final_url).path
    return path == "/404" or path.startswith("/404/")


def _title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return (m.group(1).strip() if m else "")[:40]


def _with_url(streams: list | None) -> list[dict]:
    return [s for s in streams or [] if s.get("masterUrl")]


def _page_image(item: dict, key: str) -> str:
    return item.get(key) or item.get("urlDefault") or item.get("url") or ""


def _short_side(stream: dict) -> int:
    return min(stream.get("width") or 0, stream.get("height") or 0)


@dataclass
class _VideoTracks:
    url: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    formats: list[FormatInfo] = field(default_factory=list)


def _video_tracks(video: dict) -> _VideoTracks:
    """h264 里挑分辨率最高的做默认（浏览器能直接播），其余分辨率各一档、h265 一档放进 formats。"""
    stream = (video.get("media") or {}).get("stream") or {}
    h264 = sorted(
        _with_url(stream.get("h264")),
        key=lambda s: (
            (s.get("width") or 0) * (s.get("height") or 0),
            s.get("videoBitrate") or s.get("avgBitrate") or 0,
        ),
        reverse=True,
    )
    tracks = _VideoTracks(duration=float((video.get("capa") or {}).get("duration") or 0))
    if not h264:
        return tracks
    best = h264[0]
    tracks.url = best["masterUrl"]
    tracks.width, tracks.height = best.get("width") or 0, best.get("height") or 0
    tracks.duration = float(best.get("duration") or 0) / 1000 or tracks.duration

    seen = {(tracks.width, tracks.height)}
    for s in h264[1:]:
        size = (s.get("width") or 0, s.get("height") or 0)
        if size not in seen:
            seen.add(size)
            tracks.formats.append(
                FormatInfo(
                    label=f"{min(size)}p", url=s["masterUrl"], height=min(size), filesize=int(s.get("size") or 0)
                )
            )
    if h265 := _with_url(stream.get("h265")):
        short = _short_side(h265[0])
        tracks.formats.append(
            FormatInfo(
                label=f"{short}p H.265",
                url=h265[0]["masterUrl"],
                height=short,
                filesize=int(h265[0].get("size") or 0),
                codec="H.265",
            )
        )
    return tracks


class _LoginWall(ValueError):
    """机房 / 边缘 IP 打开桌面页被要求登录; 归类成 login 让前端提示站长, 但先换手机页再说"""


class _SecurityLimit(ParseError):
    def __init__(self, code: str = "300013") -> None:
        cause = "IP 存在风险" if code == "300012" else "访问频繁"
        super().__init__("blocked", f"小红书安全限制：{cause}，错误码 {code}")


class _WrongNote(ParseError):
    def __init__(self) -> None:
        super().__init__("restricted", "小红书返回的笔记与请求不一致")


class _Expired(ValueError):
    """链接过期或笔记不存在, 换路也没用"""
