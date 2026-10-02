import json
import re
from urllib.parse import urljoin, urlparse

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
# 网页版 / 落地页链接里的作品 ID：www.kuaishou.com/short-video/<id>、live.kuaishou.com/u/<用户>/<id>、
# c.kuaishou.com 或 v.m.chenzhongtech.com 的 /fw/photo/<id>、/fw/long-video/<id>
_PHOTO_ID = re.compile(r"/(?:short-video|fw/photo|fw/long-video|u/[^/?#]+|profile/[^/?#]+)/([0-9A-Za-z]{10,})")
# 网页版分享的短链 www.kuaishou.com/f/<码>：最多跟几跳；跳到首页 / 推荐页说明链接失效了
_WEB_SHORT_LINK = re.compile(r"kuaishou\.com/f/[\w-]+")
_MAX_HOPS = 4
_HOME_PATHS = ("", "new-reco", "brilliant")
_BLOCK_MARKERS = ("验证", "captcha", "滑块", "安全", "访问频繁")
# 只替换处在"值"位置上的 undefined（冒号 / 逗号 / 左方括号之后），不碰字符串里的同名文字
_UNDEFINED = re.compile(r"(?<=[:,\[])\s*undefined(?=\s*[,\]}])")


def _json_after(html: str, marker: str):
    """从 marker 之后的第一个 { 开始按 JSON 语法读到配对的 }。

    原来用正则 `INIT_STATE = (.*?)</script>` 截取，赋值语句后面只要多跟一句
    JS，或者字符串里出现 </script>，截出来的就不是合法 JSON。
    """
    at = html.find(marker)
    if at < 0:
        return None
    start = html.find("{", at + len(marker))
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        return decoder.raw_decode(html, start)[0]
    except json.JSONDecodeError:
        pass
    # 快手的 INIT_STATE 偶尔带 JS 的 undefined，换成 null 再读一次
    try:
        return decoder.raw_decode(_UNDEFINED.sub("null", html[start:]))[0]
    except json.JSONDecodeError:
        return None


def _ext_music(block) -> str:
    """图集 / 单图作品的配乐：ext_params 里给的是相对路径 + musicCdnList。"""
    if not isinstance(block, dict):
        return ""
    path, cdns = block.get("music"), block.get("musicCdnList") or []
    if not path or not cdns or not isinstance(cdns[0], dict) or not cdns[0].get("cdn"):
        return ""
    return f"https://{cdns[0]['cdn']}/{path.lstrip('/')}"


def _short_side(width: int, height: int) -> int:
    return min(width, height) if width and height else max(width, height)


class KuaiShou(BaseParser):
    """
    快手
    """

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        headers = {"User-Agent": self.ua("iOS"), "Referer": "https://v.kuaishou.com/"}

        if "v.kuaishou.com" not in share_url:
            # 网页版 / 落地页的作品链接：直接拿作品 ID 请求手机落地页，不带分享参数和 cookie 也能拿到
            photo_id = await self._photo_id(share_url, headers)
            return await self._landing(f"https://c.kuaishou.com/fw/photo/{photo_id}", headers, None)

        # 短链不跟跳转：要拿 Location 和快手种下的第一份 cookie
        async with create_async_client(follow_redirects=False) as client:
            share_response = await client.get(share_url, headers=headers)

        location_url = share_response.headers.get("location", "")
        if not location_url:
            raise ParseError("deleted", "快手短链没有返回跳转地址")

        # /fw/long-video/ 返回结果不一样, 统一替换为 /fw/photo/ 请求
        location_url = location_url.replace("/fw/long-video/", "/fw/photo/")
        return await self._landing(location_url, headers, share_response.cookies)

    async def _photo_id(self, url: str, headers: dict) -> str:
        """链接里直接有作品 ID 就用；网页版分享出来的 www.kuaishou.com/f/... 短链跟着跳转找。

        2026-10 统计里一天有 4 个人贴了取不出 ID 的快手链接，以前一律报「不支持」。跳转经国内中继
        （www.kuaishou.com 海外直连不上，走中继能通）。跳回首页的是失效 / 不存在的短链。
        主页之类本来就不是作品的链接不跟，直接说不支持。
        """
        if m := _PHOTO_ID.search(url):
            return m.group(1)
        if not _WEB_SHORT_LINK.search(url):
            raise ParseError("unsupported", "快手链接里没有作品 ID，请用 App 里「分享 → 复制链接」的链接")
        for _ in range(_MAX_HOPS):
            async with create_async_client(follow_redirects=False) as client:
                response = await client.get(url, headers=headers)
            location = response.headers.get("location", "")
            if not location:
                break
            url = urljoin(url, location)
            if m := _PHOTO_ID.search(url):
                return m.group(1)
            if urlparse(url).path.strip("/") in _HOME_PATHS:
                raise ParseError("deleted", "快手短链已失效，跳回了首页")
        raise ParseError("parse", f"快手短链没有跳到作品页（停在 {url[:80]}）")

    async def _landing(self, location_url: str, headers: dict, cookies) -> VideoInfo:
        # 同一个 UA、带上 Referer 和那份 cookie 去请求落地页——快手拿 cookie 认会话，
        # 少了它落地页会返回空壳。以前这里把第一跳的*响应头*原样当请求头发出去了
        # （content-type / location / set-cookie ...），UA 和 Referer 反而都没带
        # 落地页偶尔（实测约 1/40）回一个没有 INIT_STATE、连标题都没有的空页面，
        # 马上再要一次就正常。验证页不重试，那是真被限流了
        for _ in range(2):
            async with create_async_client(follow_redirects=True) as client:
                response = await client.get(location_url, headers=headers, cookies=cookies)
            html = response.text
            state = _json_after(html, "window.INIT_STATE")
            if isinstance(state, dict):
                return self._build(state)
            error = self._block_error(html)
            if error.reason == "blocked":
                break
        raise error

    @staticmethod
    def _block_error(html: str) -> ParseError:
        """落地页里没有 INIT_STATE 时，说清楚快手到底返回了什么。"""
        title = (m.group(1).strip() if (m := _TITLE.search(html)) else "") or "无"
        if any(k in html[:20000] for k in _BLOCK_MARKERS):
            return ParseError("blocked", f"快手返回了验证页（标题: {title}）")
        return ParseError("parse", f"页面里没有 INIT_STATE（标题: {title}）")

    @staticmethod
    def _build(state: dict) -> VideoInfo:
        # INIT_STATE 是个以随机 key 组织的字典，作品数据在同时含 result 和 photo 的那一项
        entry = next((v for v in state.values() if isinstance(v, dict) and "result" in v and "photo" in v), None)
        if entry is None:
            raise ParseError("parse", "INIT_STATE 里没有作品数据")

        result = entry.get("result")
        if result != 1:
            if result in (2, 400002):
                raise ParseError("deleted", f"快手 result={result}")
            raise ParseError("restricted", f"快手 result={result}")

        data = entry.get("photo") or {}

        mv = data.get("mainMvUrls") or []
        video_url = mv[0].get("url", "") if mv else ""

        # 图集：cdn + 相对路径拼出来
        ext = data.get("ext_params") or {}
        atlas = ext.get("atlas") or {}
        cdns, paths = atlas.get("cdn") or [], atlas.get("list") or []
        images = [ImgInfo(url=f"https://{cdns[0]}/{p}") for p in paths if isinstance(p, str)] if cdns else []
        covers = data.get("coverUrls") or data.get("webpCoverUrls") or []
        # 单图作品（photoType SINGLE_PICTURE，singlePicture: true）：没有视频也没有 atlas，
        # 那张图就是封面——/upic/ 下用户传的原图，和作品宽高一致；配乐在 ext_params.single。
        # 以前这种作品直接报「没有拿到任何视频或图片」
        if not images and not video_url and (data.get("singlePicture") or data.get("photoType") == "SINGLE_PICTURE"):
            cover = next((c.get("url") for c in covers if isinstance(c, dict) and c.get("url")), "")
            if cover:
                images = [ImgInfo(url=cover.replace("http://", "https://", 1))]

        # 有的作品在 manifest 里给了多档清晰度
        formats = []
        adaptation = ((data.get("manifest") or {}).get("adaptationSet") or [{}])[0] or {}
        for rep in adaptation.get("representation") or []:
            url = rep.get("url") or ""
            if not url or url == video_url:
                continue
            short = _short_side(int(rep.get("width") or 0), int(rep.get("height") or 0))
            # 同一清晰度常给 avc / hevc 两份，不标出来就是两个一模一样的"720p"
            codec = "H.265" if rep.get("videoCodec") == "hevc" else ""
            label = (f"{short}p" if short else (rep.get("qualityLabel") or "其他")) + (f" {codec}" if codec else "")
            formats.append(
                FormatInfo(label=label, url=url, height=short, filesize=int(rep.get("fileSize") or 0), codec=codec)
            )

        return VideoInfo(
            # 图集作品的 mainMvUrls 是配乐的视频壳，不是作品本身
            video_url="" if images else video_url,
            cover_url=covers[0].get("url", "") if covers else (data.get("coverUrl") or ""),
            title=data.get("caption") or "",
            author=VideoAuthor(
                uid=str(data.get("userEid") or data.get("userId") or ""),
                name=data.get("userName") or "",
                avatar=data.get("headUrl") or "",
            ),
            images=images,
            music_url=_ext_music(atlas) or _ext_music(ext.get("single")),
            duration=(data.get("duration") or 0) / 1000,
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            formats=formats,
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise NotImplementedError("快手暂不支持直接解析视频ID")
