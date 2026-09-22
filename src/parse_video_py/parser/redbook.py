import json
import os
import re
from urllib.parse import urlparse

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo

# 原图: ci.xiaohongshu.com 上按 key 取, w/0 表示不缩放; 页面里给的都是缩到 1080 宽的版本
_ORIGINAL_IMAGE = "https://ci.xiaohongshu.com/{key}?imageView2/2/w/0/format/jpg/q/90"

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>", re.DOTALL)


class RedBook(BaseParser):
    """
    小红书

    两条路：
    - 桌面页 (note.noteDetailMap)：住宅 IP 或带登录 cookie 时可用，视频档位最全
    - 手机分享页 (noteData.data.noteData)：给"在手机浏览器里打开分享链接"用的落地页，
      机房 / 边缘 IP 不要求登录。桌面页被要求登录时自动切过来，之后优先走这条。
    """

    _prefer_mobile = False   # 桌面页撞过一次登录墙后就先走手机页

    @staticmethod
    def _mobile_first() -> bool:
        """走中继又没配 cookie 时，桌面页必然是登录墙，别拿一次跨洋往返去撞。

        中继的出口是边缘节点，也就是机房 IP，小红书对这类出口一律要求登录。
        _prefer_mobile 只是进程内的记忆，重启就忘，等于每次重启后的第一个用户
        都要替后面的人垫一次白跑的请求。配了 PARSE_VIDEO_XHS_COOKIE 就仍然先走
        桌面页——那条路的视频档位更全。
        """
        from ..convert import relay
        return relay.enabled() and not os.getenv("PARSE_VIDEO_XHS_COOKIE")

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        mobile_first = RedBook._prefer_mobile or self._mobile_first()
        order = ("mobile", "desktop") if mobile_first else ("desktop", "mobile")
        last_error: Exception | None = None
        for mode in order:
            try:
                return await (self._parse_mobile if mode == "mobile" else self._parse_desktop)(share_url)
            except _LoginWall as e:
                RedBook._prefer_mobile = True
                last_error = e
            except _Expired:
                raise
            except Exception as e:  # noqa: BLE001 - 换一条路再试
                last_error = e
        raise last_error or ValueError("小红书解析失败")

    # ------------------------------------------------------------------ 桌面页
    async def _parse_desktop(self, share_url: str) -> VideoInfo:
        headers = {
            "User-Agent": _DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        # 海外 / 机房 IP 经常拿到登录页, 带上登录后的 cookie 就正常了
        if cookie := os.getenv("PARSE_VIDEO_XHS_COOKIE"):
            headers["Cookie"] = cookie
        final_url, html = await self._get(share_url, headers)
        json_data = self._state(final_url, html)
        if "note" not in json_data:
            raise self._block_error(final_url, html)
        note = json_data["note"]
        note_id = note.get("currentNoteId")
        # 验证返回：小红书的分享链接有有效期，过期后会返回 undefined
        if not note_id or note_id == "undefined":
            raise _Expired("parse fail: note id in response is undefined")
        data = ((note.get("noteDetailMap") or {}).get(note_id) or {}).get("note")
        if not data:
            raise _Expired("parse fail: note detail is empty (链接可能过期或需要 xsec_token)")
        return self._build(data, image_url_key="urlDefault", nick_key="nickname")

    # ------------------------------------------------------------------ 手机分享页
    async def _parse_mobile(self, share_url: str) -> VideoInfo:
        headers = {
            "User-Agent": _MOBILE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if cookie := os.getenv("PARSE_VIDEO_XHS_COOKIE"):
            headers["Cookie"] = cookie
        final_url, html = await self._get(share_url, headers)
        json_data = self._state(final_url, html)
        data = ((json_data.get("noteData") or {}).get("data") or {}).get("noteData")
        if not data:
            if "/404" in final_url or "sec_" in final_url:
                raise _Expired("链接过期或笔记不存在")
            raise self._block_error(final_url, html)
        return self._build(data, image_url_key="url", nick_key="nickName")

    # ------------------------------------------------------------------ 公共
    async def _get(self, url: str, headers: dict) -> tuple[str, str]:
        async with create_async_client(follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        final_url = str(response.url)
        if "/login" in urlparse(final_url).path:
            raise _LoginWall("小红书要求这个出口登录")
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
        # 视频: h264 里挑分辨率最高的做默认 (浏览器能直接播), 其余档位和 h265 放进 formats
        video_url = ""
        formats: list[FormatInfo] = []
        width = height = 0
        duration = 0.0
        stream = ((data.get("video") or {}).get("media") or {}).get("stream") or {}
        h264 = [s for s in stream.get("h264") or [] if s.get("masterUrl")]
        if h264:
            h264.sort(key=lambda s: ((s.get("width") or 0) * (s.get("height") or 0), s.get("videoBitrate") or s.get("avgBitrate") or 0), reverse=True)
            best = h264[0]
            video_url = best["masterUrl"]
            width, height = best.get("width") or 0, best.get("height") or 0
            duration = float(best.get("duration") or 0) / 1000
            seen = {(width, height)}
            for s in h264[1:]:
                key = (s.get("width") or 0, s.get("height") or 0)
                if key in seen:
                    continue
                seen.add(key)
                formats.append(FormatInfo(label=f"{min(key)}p", url=s["masterUrl"], height=min(key), filesize=int(s.get("size") or 0)))
            for s in stream.get("h265") or []:
                if s.get("masterUrl"):
                    short = min(s.get("width") or 0, s.get("height") or 0)
                    formats.append(FormatInfo(label=f"{short}p H.265", url=s["masterUrl"], height=short,
                                              filesize=int(s.get("size") or 0), codec="H.265"))
                    break
        if not duration:
            duration = float(((data.get("video") or {}).get("capa") or {}).get("duration") or 0)

        # 图集: 原图分辨率; 实况图带上短视频
        images: list[ImgInfo] = []
        image_list = data.get("imageList") or []
        if not video_url:
            for item in image_list:
                page_img = item.get(image_url_key) or item.get("urlDefault") or item.get("url") or ""
                if not page_img:
                    continue
                key = item.get("fileId") or self._image_key(page_img)
                img = ImgInfo(url=_ORIGINAL_IMAGE.format(key=key) if key else page_img)
                live = [s for s in ((item.get("stream") or {}).get("h264") or []) if s.get("masterUrl")]
                if item.get("livePhoto") and live:
                    img.live_photo_url = live[0]["masterUrl"]
                images.append(img)

        cover = ""
        if image_list:
            cover = image_list[0].get(image_url_key) or image_list[0].get("urlDefault") or image_list[0].get("url") or ""
        user = data.get("user") or {}
        return VideoInfo(
            video_url=video_url,
            cover_url=cover,
            title=data.get("title") or (data.get("desc") or "")[:60],
            images=images,
            duration=duration,
            width=width,
            height=height,
            formats=formats,
            author=VideoAuthor(
                uid=user.get("userId", ""),
                name=user.get(nick_key) or user.get("nickname") or user.get("nickName") or "",
                avatar=user.get("avatar", ""),
            ),
        )

    @staticmethod
    def _block_error(final_url: str, html: str) -> Exception:
        """页面不是笔记时, 说清楚小红书到底返回了什么, 站长才知道该配 cookie 还是代理。"""
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        title_text = (title.group(1).strip() if title else "")[:40]
        if "/404" in final_url or "sec_" in final_url:
            return _Expired("链接过期或笔记不存在 (小红书跳到了 404)")
        if "/login" in final_url:
            return _LoginWall("小红书要求这个出口登录")
        markers = ("验证", "captcha", "verify", "安全", "网络连接异常", "海外")
        if any(m in html[:20000] or m in title_text for m in markers):
            return ValueError("小红书对服务器所在网络返回了验证页 (被限流)，海外服务器请配置 "
                              "PARSE_VIDEO_PROXY_CN / PARSE_VIDEO_RELAY_CN 或 PARSE_VIDEO_XHS_COOKIE")
        return ValueError(f"小红书返回了意外页面 (标题: {title_text or '无'}，地址: {final_url[:80]})")

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise NotImplementedError("小红书暂不支持直接解析视频ID")


class _LoginWall(ValueError):
    """机房 / 边缘 IP 打开桌面页被要求登录; 归类成 login 让前端提示站长, 但先换手机页再说"""


class _Expired(ValueError):
    """链接过期或笔记不存在, 换路也没用"""
