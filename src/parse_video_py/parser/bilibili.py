import asyncio
import base64
import json
import os
import random
import string
from urllib.parse import urlencode, urlparse

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, VideoAuthor, VideoInfo
from .errors import ParseError

_REFERER = "https://www.bilibili.com/"

# 清晰度 id -> 高度。html5 合一流只给 quality，不给宽高
_QN_HEIGHT = {6: 240, 16: 360, 32: 480, 64: 720, 74: 720, 80: 1080, 112: 1080, 116: 1080,
              120: 2160, 125: 2160, 126: 2160, 127: 4320}


class BiliBili(BaseParser):
    """
    哔哩哔哩

    以前的做法是 html5 合一流（封顶 720p）+ 同时让 yt-dlp 跑一遍列出高清档位，
    整次解析要等 yt-dlp，实测 ~1100ms。高清档位其实就在 DASH 接口里，自己请求
    就行：

    - cid 从 pagelist 拿（两百来字节，比 view 快），拿到马上并发要 DASH 和合一流；
      view 在旁边并行取标题、作者
    - 走 x/player/wbi/playurl：老的 x/player/playurl 风控严得多，同一出口高频
      调用后稳定 412，同一时刻新接口照常返回、内容一致。目前不校验 WBI 签名；
      哪天开始校验，上层会退回 yt-dlp（它自己会签）
    - 未登录带上 try_look + 浏览器指纹参数，DASH 照样给到 1080p（见 guest_params）
    - 高清档位的下载仍交给 yt-dlp 按 format_spec 取，下载流程不变
    """

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )

    _buvid_cookie: str = ""   # 进程内缓存一份, 免得每次都去要

    def get_default_headers(self) -> dict:
        headers = {
            "User-Agent": self.USER_AGENT,
            "Referer": _REFERER,
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        # 登录 cookie 可拿更高清晰度; 没有的话用 buvid 也能避开机房 IP 的 412
        cookie = os.getenv("PARSE_VIDEO_BILI_COOKIE") or BiliBili._buvid_cookie
        if cookie:
            headers["Cookie"] = cookie
        return headers

    async def _ensure_buvid(self) -> None:
        """机房 / 海外 IP 不带 buvid3 直接请求 API 会被 412, 先领一份设备指纹 cookie。"""
        if BiliBili._buvid_cookie or os.getenv("PARSE_VIDEO_BILI_COOKIE"):
            return
        try:
            async with create_async_client() as client:
                resp = await client.get(
                    "https://api.bilibili.com/x/frontend/finger/spi",
                    headers={"User-Agent": self.USER_AGENT, "Referer": _REFERER},
                )
            data = resp.json().get("data") or {}
            if data.get("b_3"):
                BiliBili._buvid_cookie = f"buvid3={data['b_3']}; buvid4={data.get('b_4', '')}"
        except Exception:  # noqa: BLE001 - 拿不到就裸请求试试
            pass

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        bvid = await self._get_bvid_from_url(share_url)
        return await self.parse_video_id(bvid)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        await self._ensure_buvid()

        async def play_by_pagelist():
            cid = await self._page_cid(video_id)
            return await self._play_urls(video_id, cid) if cid else None

        view, early = await asyncio.gather(
            self._api(f"https://api.bilibili.com/x/web-interface/view?{id_param(video_id, 'aid')}"),
            play_by_pagelist(),
        )
        code = view.get("code")
        if code != 0:
            msg = view.get("message", "")
            if code in (-404, 62002, 62004):
                raise ParseError("deleted", msg)
            raise ParseError("restricted", f"B 站返回 code={code} {msg}")
        data = view.get("data") or {}

        if early is None:
            # pagelist 没拿到, 退回 view 里的 cid 再要一次
            cid = ((data.get("pages") or [{}])[0]).get("cid")
            if not cid:
                raise ParseError("parse", "view 接口没有返回 cid")
            early = await self._play_urls(video_id, cid)
        dash, html5 = early

        if isinstance(dash, BaseException) and isinstance(html5, BaseException):
            raise dash
        dash = {} if isinstance(dash, BaseException) else dash
        html5 = {} if isinstance(html5, BaseException) else html5

        html5_data = html5.get("data") or {}
        video_url = ((html5_data.get("durl") or [{}])[0]).get("url", "")
        height = _QN_HEIGHT.get(html5_data.get("quality") or 0, 0)
        duration = float(data.get("duration") or 0)
        formats = dash_formats(dash.get("data") or {}, duration, above=height)
        if not video_url and not formats:
            code = dash.get("code") or html5.get("code")
            msg = dash.get("message") or html5.get("message") or ""
            raise ParseError("restricted", f"B 站播放接口 code={code} {msg}")

        owner = data.get("owner") or {}
        return VideoInfo(
            title=data.get("title", ""),
            video_url=video_url,
            cover_url=data.get("pic", ""),
            duration=duration,
            height=height,
            formats=formats,
            # 高清档位下载时 yt-dlp 按这个地址重新取
            page_url=f"https://www.bilibili.com/video/{data.get('bvid') or video_id}",
            author=VideoAuthor(
                uid=str(owner.get("mid", "")),
                name=owner.get("name", ""),
                avatar=owner.get("face", ""),
            ),
        )

    async def _page_cid(self, video_id: str) -> int | None:
        """第一 P 的 cid。拿不到返回 None，由调用方退回 view 里的那份。"""
        try:
            resp = await self._api(f"https://api.bilibili.com/x/player/pagelist?{id_param(video_id, 'aid')}")
            return ((resp.get("data") or [{}])[0]).get("cid") or None
        except Exception:  # noqa: BLE001
            return None

    async def _play_urls(self, video_id: str, cid: int):
        """DASH 分轨和 html5 合一流, 两者只依赖 cid, 一起发。异常原样放在结果里。"""
        base = f"https://api.bilibili.com/x/player/wbi/playurl?{id_param(video_id, 'avid')}&cid={cid}"
        dash = f"{base}&qn=127&fnval=4048&fnver=0&fourk=1"
        if not os.getenv("PARSE_VIDEO_BILI_COOKIE"):
            dash += "&" + urlencode(guest_params())
        return await asyncio.gather(
            self._api(dash),
            self._api(f"{base}&qn=80&fnval=0&fnver=0&platform=html5"),
            return_exceptions=True,
        )

    async def _get_bvid_from_url(self, raw_url: str) -> str:
        """从URL中提取 BV 号（或老的 av 号）"""
        try:
            parsed_url = urlparse(raw_url)
        except Exception:
            raise ValueError("URL格式无效")

        if "b23.tv" in parsed_url.netloc:
            # 处理短链接
            async with create_async_client(follow_redirects=False) as client:
                resp = await client.get(raw_url, headers=self.get_default_headers())
                location = resp.headers.get("location")
                if not location:
                    raise ValueError("无法从b23.tv获取重定向链接")
                return await self._get_bvid_from_url(location)

        if "bilibili.com" in parsed_url.netloc:
            for part in parsed_url.path.split("/"):
                if part[:2] in ("BV", "bv") or (part[:2] in ("av", "AV") and part[2:].isdigit()):
                    return part

        raise ValueError("不是有效的B站视频链接")

    async def _api(self, api_url: str) -> dict:
        """发送B站API请求"""
        async with create_async_client() as client:
            response = await client.get(api_url, headers=self.get_default_headers())
            if response.status_code == 412:
                # 风控: 换一份 buvid 再试一次
                BiliBili._buvid_cookie = ""
                await self._ensure_buvid()
                response = await client.get(api_url, headers=self.get_default_headers())
            if response.status_code == 412:
                raise ValueError("B站拒绝了服务器所在网络的访问 (412)，海外服务器请配置 PARSE_VIDEO_PROXY_CN 或 PARSE_VIDEO_BILI_COOKIE")
            if response.status_code != 200:
                raise ValueError(f"HTTP请求失败, 状态码: {response.status_code}")
            return response.json()


def id_param(video_id: str, aid_key: str) -> str:
    """BV 号走 bvid=；老的 av 号要换成纯数字——把 av170001 原样塞给 bvid 只会得到 -400。

    av 号的参数名各接口不统一：view / pagelist 认 aid，playurl 只认 avid。
    """
    if video_id[:2] in ("av", "AV") and video_id[2:].isdigit():
        return f"{aid_key}={video_id[2:]}"
    return f"bvid={video_id}"


def guest_params() -> dict:
    """未登录也要到 720p / 1080p 的 DASH 档位。

    try_look=1 是 B 站给游客"试看高清"的开关，但只有同时带上浏览器指纹参数
    (dm_*) 才生效，缺一个就只给 360 / 480。实测带上之后 1080p 照给，WBI 签名
    反而不是必须的。做法照 yt-dlp（来源是 B 站自己的 bili-user-fingerprint.js）：
    这些值本来就是前端随机造的，鼠标轨迹那两项留空也能过。
    """
    def noise(lo: int, hi: int) -> str:
        raw = "".join(random.choices(string.printable, k=random.randint(lo, hi))).encode()
        return base64.b64encode(raw)[:-2].decode()

    r, o, top = random.randint(0, 113), random.randint(0, 513), random.randint(0, 100)
    return {
        "try_look": 1,
        "dm_img_list": "[]",
        "dm_img_str": noise(16, 64),
        "dm_cover_img_str": noise(32, 128),
        # 屏幕 1920x1080 时的 wh / of 编码，必须是没有空格的紧凑 JSON
        "dm_img_inter": json.dumps(
            {"ds": [], "wh": [2 * 1920 + 2 * 1080 + 3 * r, 4 * 1920 - 1080 + r, r],
             "of": [3 * top + o, 4 * top + 2 * o, o]},
            separators=(",", ":"),
        ),
    }


def dash_formats(play_data: dict, duration: float, above: int) -> list[FormatInfo]:
    """DASH 里比合一流更清晰的档位, 每个高度一档, 外加仅音频。

    format_spec 和 yt-dlp 站点用的是同一种写法, 下载时照旧交给 yt-dlp 合并。
    体积按码率 × 时长估算。
    """
    dash = play_data.get("dash") or {}
    audios = dash.get("audio") or []
    audio_bw = max((a.get("bandwidth") or 0 for a in audios), default=0)

    # 短边 -> (真实高度, 最大码率)。竖屏视频的"1080p"是宽 1080、高 1920：
    # 档位名按短边叫，交给 yt-dlp 的筛选条件得用真实高度
    tiers: dict[int, tuple[int, int]] = {}
    for v in dash.get("video") or []:
        w, h = v.get("width") or 0, v.get("height") or 0
        short = min(w, h) if w and h else h
        if short > above:
            _, bw = tiers.get(short, (h, 0))
            tiers[short] = (h, max(bw, v.get("bandwidth") or 0))

    formats = [
        FormatInfo(
            label=f"{short}p",
            format_spec=f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]",
            ext="mp4",
            height=short,
            filesize=int((bw + audio_bw) * duration / 8),
        )
        for short, (h, bw) in sorted(tiers.items(), reverse=True)
    ]
    if audios:
        formats.append(FormatInfo(label="仅音频", format_spec="ba[ext=m4a]/ba", ext="m4a", height=0,
                                  filesize=int(audio_bw * duration / 8)))
    return formats
