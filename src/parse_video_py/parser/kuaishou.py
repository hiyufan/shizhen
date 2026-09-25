import json
import re

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
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


def _short_side(width: int, height: int) -> int:
    return min(width, height) if width and height else max(width, height)


class KuaiShou(BaseParser):
    """
    快手
    """

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        headers = {"User-Agent": self.ua("iOS"), "Referer": "https://v.kuaishou.com/"}

        # 短链不跟跳转：要拿 Location 和快手种下的第一份 cookie
        async with create_async_client(follow_redirects=False) as client:
            share_response = await client.get(share_url, headers=headers)

        location_url = share_response.headers.get("location", "")
        if not location_url:
            raise ParseError("deleted", "快手短链没有返回跳转地址")

        # /fw/long-video/ 返回结果不一样, 统一替换为 /fw/photo/ 请求
        location_url = location_url.replace("/fw/long-video/", "/fw/photo/")

        # 同一个 UA、带上 Referer 和那份 cookie 去请求落地页——快手拿 cookie 认会话，
        # 少了它落地页会返回空壳。以前这里把第一跳的*响应头*原样当请求头发出去了
        # （content-type / location / set-cookie ...），UA 和 Referer 反而都没带
        # 落地页偶尔（实测约 1/40）回一个没有 INIT_STATE、连标题都没有的空页面，
        # 马上再要一次就正常。验证页不重试，那是真被限流了
        for attempt in range(2):
            async with create_async_client(follow_redirects=True) as client:
                response = await client.get(location_url, headers=headers, cookies=share_response.cookies)
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
        atlas = (data.get("ext_params") or {}).get("atlas") or {}
        cdns, paths = atlas.get("cdn") or [], atlas.get("list") or []
        images = [ImgInfo(url=f"https://{cdns[0]}/{p}") for p in paths if isinstance(p, str)] if cdns else []

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
            formats.append(FormatInfo(label=label, url=url, height=short, filesize=int(rep.get("fileSize") or 0),
                                      codec=codec))

        covers = data.get("coverUrls") or data.get("webpCoverUrls") or []
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
            duration=(data.get("duration") or 0) / 1000,
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            formats=formats,
        )

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise NotImplementedError("快手暂不支持直接解析视频ID")
