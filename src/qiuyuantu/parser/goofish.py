"""闲鱼商品公开图片与视频；使用 H5 页面的匿名令牌，不读取账号 Cookie。"""

import hashlib
import html
import json
import re
import time
from urllib.parse import parse_qs, urlparse, urlunparse

from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError

API = "mtop.taobao.idle.awesome.detail"
ENDPOINT = f"https://h5api.m.goofish.com/h5/{API}/1.0/"
APP_KEY = "12574478"
REFERER = "https://h5.m.goofish.com/"
_ITEM_ID = re.compile(r"[0-9]{1,20}\Z")
_TOKEN_ERRORS = {"FAIL_SYS_TOKEN_EMPTY", "FAIL_SYS_TOKEN_EXOIRED", "FAIL_SYS_TOKEN_EXPIRED", "FAIL_SYS_ILLEGAL_ACCESS"}


def item_id(url: str) -> str:
    parsed = urlparse(url)
    routes = {
        "www.goofish.com": {"/item"},
        "goofish.com": {"/item"},
        "h5.m.goofish.com": {"/item"},
        "2.taobao.com": {"/item.htm"},
    }
    if parsed.scheme not in {"http", "https"} or parsed.path not in routes.get(parsed.hostname, set()):
        raise ParseError("unsupported", "请使用闲鱼商品链接；淘宝和天猫商品暂不支持")
    query = parse_qs(parsed.query)
    ident = (query.get("id") or query.get("itemId") or [""])[0]
    if not _ITEM_ID.fullmatch(ident):
        raise ParseError("unsupported", "闲鱼链接中没有有效的商品 ID")
    if query.get("id") and query.get("itemId") and query["id"][0] != query["itemId"][0]:
        raise ParseError("unsupported", "闲鱼链接中的商品 ID 不一致")
    return ident


def _media(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse("https:" + url if url.startswith("//") else url)
    host = parsed.hostname or ""
    allowed = any(
        host == domain or host.endswith("." + domain) for domain in ("alicdn.com", "taobaocdn.com", "video.taobao.com")
    )
    if parsed.scheme not in {"http", "https"} or not allowed or parsed.username or parsed.port:
        raise ParseError("parse", "闲鱼返回的媒体地址不可用")
    # API 中的 HEIC 路径也可能返回 JPEG；保留文件名与查询参数，由实际响应类型决定保存格式。
    return urlunparse(parsed._replace(scheme="https", fragment=""))


def _result(data: dict, ident: str) -> VideoInfo:
    item = data.get("itemDO") or {}
    if str(item.get("itemId") or "") != ident:
        raise ParseError("parse", "闲鱼没有返回对应商品的信息")
    images = list(dict.fromkeys(_media(row["url"]) for row in item.get("imageInfos") or [] if row.get("url")))
    video = item.get("videoPlayInfo") or {}
    videos = list(dict.fromkeys(_media(video[key]) for key in ("playUrl", "ld320pUrl") if video.get(key)))
    if not images and not videos:
        raise ParseError("empty", "闲鱼没有提供可下载的公开图片或视频")
    seller = data.get("sellerDO") or {}
    return VideoInfo(
        video_url=videos[0] if videos else "",
        cover_url=_media(video.get("url") or "") or next(iter(images), ""),
        title=item.get("title") or "闲鱼",
        images=[ImgInfo(url=url) for url in images],
        formats=[FormatInfo(label="MP4", url=url) for url in videos],
        author=VideoAuthor(name=seller.get("nick") or ""),
        page_url=f"https://www.goofish.com/item?id={ident}",
        video_headers={"Referer": REFERER},
        notice="闲鱼公开图片可能自带水印，按平台提供的文件保存。" if images else "",
    )


def _check_response(reply: dict) -> dict:
    statuses = reply.get("ret") or []
    if any(status.startswith("SUCCESS::") for status in statuses):
        return reply.get("data") or {}
    status = " ".join(statuses)
    # 不直接回传服务端错误详情，其中可能夹带验证跳转或会话参数。
    if re.search(r"SESSION|LOGIN|登录", status, re.I):
        raise ParseError("login", "闲鱼暂时要求登录后查看这条商品")
    if re.search(r"NOT_EXIST|DELETED|OFF_SHELF|不存在|已删除|已下架", status, re.I):
        raise ParseError("deleted", "闲鱼商品已下架或不可公开访问")
    if re.search(r"TOKEN|ILLEGAL_ACCESS|RGV|VALIDATE|USER_VALIDATE|LIMIT|验证|流量", status, re.I):
        raise ParseError("blocked", "闲鱼暂时没有开放这条商品的匿名访问")
    raise ParseError("parse", "闲鱼暂时没有返回商品信息")


class Goofish(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        parsed = urlparse(share_url)
        if parsed.hostname == "m.tb.cn":
            if parsed.scheme not in {"http", "https"} or not re.fullmatch(r"/h\.[A-Za-z0-9]{1,40}/?", parsed.path):
                raise ParseError("unsupported", "闲鱼分享短链无效")
            response = await self.fetch("GET", share_url, follow_redirects=True)
            target = str(response.url)
            if urlparse(target).hostname == "m.tb.cn":
                match = re.search(r"\bvar\s+url\s*=\s*(['\"])(.*?)\1\s*;", response.text)
                if not match:
                    raise ParseError("unsupported", "分享短链已失效，请重新复制闲鱼商品链接")
                target = html.unescape(match[2].replace(r"\/", "/"))
            return await self.parse_video_id(item_id(target))
        return await self.parse_video_id(item_id(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not _ITEM_ID.fullmatch(video_id):
            raise ParseError("unsupported", "闲鱼商品 ID 无效")
        data = json.dumps({"itemId": video_id}, separators=(",", ":"))
        cookies: dict[str, str] = {}
        for _ in range(2):
            reply, received = await self._request(data, cookies)
            codes = {status.split("::", 1)[0] for status in reply.get("ret") or []}
            if not codes.intersection(_TOKEN_ERRORS) or not received.get("_m_h5_tk"):
                return _result(_check_response(reply), video_id)
            cookies.update(received)
        return _result(_check_response(reply), video_id)

    async def _request(self, data: str, cookies: dict[str, str]) -> tuple[dict, dict[str, str]]:
        timestamp = str(int(time.time() * 1000))
        token = cookies.get("_m_h5_tk", "").split("_", 1)[0]
        signature = hashlib.md5(f"{token}&{timestamp}&{APP_KEY}&{data}".encode()).hexdigest()
        headers = {**self.get_default_headers(), "Referer": REFERER}
        if cookies:
            headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
        response = await self.fetch(
            "GET",
            ENDPOINT,
            headers=headers,
            params={
                "jsv": "2.7.2",
                "appKey": APP_KEY,
                "t": timestamp,
                "sign": signature,
                "api": API,
                "v": "1.0",
                "type": "originaljson",
                "dataType": "json",
                "data": data,
            },
            timeout=20,
        )
        # 连接池禁存 Cookie；只在本次解析内手动传递匿名签名令牌，不接收登录凭证。
        received = {c.name: c.value for c in response.cookies.jar if c.name in {"_m_h5_tk", "_m_h5_tk_enc"}}
        return response.json(), received
