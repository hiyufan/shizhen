import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from ..utils import gather_or_cancel
from . import image_probe
from .base import BaseParser, FormatInfo, VideoAuthor, VideoInfo
from .errors import ParseError
from .quality import direct_format, number, rank, verify_direct_formats

_API = "https://api.weishi.qq.com/trpc.weishi.weishi_h5_proxy.weishi_h5_proxy/WSH5GetPlayPage"
_REFERER = "https://isee.weishi.qq.com/"


def _matching_feed(body: dict, video_id: str) -> dict:
    if (body.get("rsp_header") or {}).get("ret") != 0:
        raise ParseError("restricted", "微视没有提供这条作品的播放信息")
    data = body.get("rsp_body") or {}
    if data.get("isdeleted"):
        raise ParseError("deleted")
    result = ((data.get("feedResultInfo") or {}).get("feedResults") or {}).get(video_id) or {}
    if result.get("ret") or data.get("errmsg"):
        raise ParseError("restricted", "微视当前没有公开这条作品的播放地址")
    # 接口同时带推荐列表；只能使用 ID 对应的 feeds，不能拿推荐或首条不相关作品顶替。
    feed = next((row for row in data.get("feeds") or [] if str(row.get("id")) == video_id), None)
    if feed is None:
        raise ParseError("restricted", "微视没有返回链接对应的作品")
    return feed


def _media_identity(url: str) -> str:
    parsed = urlparse(url)
    # 同一静态 MP4 的 wsadapt 是播放器策略标记；保留所选地址的全部签名参数用于实际下载。
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "wsadapt"]
    )
    return urlunparse(parsed._replace(query=query, fragment=""))


def _codec(row: dict) -> str:
    encoder = (row.get("externInfo") or {}).get("video_encoder")
    known = {"t264": "H.264", "t265": "H.265", "t266": "H.266", "av1": "AV1"}
    return known.get(encoder) or ("H.264" if row.get("videoCoding") == 1 else "")


async def _video_formats(feed: dict, headers: dict[str, str]) -> list[FormatInfo]:
    variants = feed.get("video_spec_urls") or {}
    if not isinstance(variants, dict) or len(variants) > 64:
        raise ParseError("parse", "微视返回的清晰度档位数量异常")
    formats = []
    for row in variants.values():
        if isinstance(row, dict) and row.get("url") and not number(row.get("haveWatermark")):
            formats.append(direct_format(row["url"], row | {"videoCodec": _codec(row)}))
    if url := feed.get("video_url"):
        formats.append(direct_format(url, feed.get("video") or {}))
    unique = {}
    for fmt in formats:
        unique.setdefault(_media_identity(fmt.url), fmt)
    if not unique:
        raise ParseError("empty", "微视没有返回可下载的视频地址")
    return await verify_direct_formats(list(unique.values()), headers, all_tracks=True)


def _preview(formats: list[FormatInfo], url: str) -> FormatInfo:
    primary = next((fmt for fmt in formats if _media_identity(fmt.url) == _media_identity(url)), None) if url else None
    return primary or max([fmt for fmt in formats if fmt.codec == "H.264"] or formats, key=rank)


def _cover_urls(feed: dict) -> list[str]:
    rows = list(feed.get("images") or [])[:4]
    cover = feed.get("video_cover") or {}
    rows.extend(cover.get(key) or {} for key in ("static_cover", "smart_cover"))
    urls = [row["url"] for row in rows if isinstance(row, dict) and row.get("url")]
    for url in list(urls):
        parsed = urlparse(url)
        # xp.qpic.cn 的 /0 已验证为原尺寸；pic.weishi.qq.com 的 /0 却会返回 120px 占位图。
        if parsed.hostname == "xp.qpic.cn" and re.search(r"/[1-9][0-9]*$", parsed.path):
            urls.append(urlunparse(parsed._replace(path=re.sub(r"/[0-9]+$", "/0", parsed.path))))
    return list(dict.fromkeys(urls))


async def _cover_url(feed: dict, headers: dict[str, str]) -> str:
    urls = _cover_urls(feed)
    candidates = [url for url in urls if urlparse(url).scheme in {"http", "https"}]
    sizes = await gather_or_cancel(*(image_probe.probe(url, headers) for url in candidates))
    verified = [(url, size) for url, size in zip(candidates, sizes, strict=True) if size]
    if verified:
        return max(verified, key=lambda row: (max(row[1]), row[1][0] * row[1][1]))[0]
    return urls[0] if urls else ""


class WeiShi(BaseParser):
    """微视"""

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        parsed = urlparse(share_url)
        if parsed.hostname == "video.weishi.qq.com":
            response = await self.fetch(
                "GET", share_url, headers={"Referer": _REFERER, "User-Agent": self.ua("windows")}, follow_redirects=True
            )
            parsed = urlparse(str(response.url))
        if parsed.hostname not in {"isee.weishi.qq.com", "h5.weishi.qq.com"}:
            raise ParseError("unsupported", "请粘贴微视作品的分享链接")
        return await self.parse_video_id((parse_qs(parsed.query).get("id") or [""])[0])

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
            raise ParseError("unsupported", "微视链接里没有有效的作品编号")
        headers = {"Referer": _REFERER, "User-Agent": self.ua("windows")}
        body = await self.post_json(
            _API,
            headers=headers,
            json={
                "req_body": {"feedid": video_id, "recommendtype": 0, "datalvl": "all", "_weishi_mapExt": {}},
                "req_header": {"personId": "", "authInfo": {"authType": 0}, "channelId": 4},
            },
        )
        feed = _matching_feed(body, video_id)
        formats, cover_url = await gather_or_cancel(_video_formats(feed, headers), _cover_url(feed, headers))
        preview = _preview(formats, feed.get("video_url") or "")
        poster = feed.get("poster") or {}
        return VideoInfo(
            video_url=preview.url,
            width=preview.width,
            height=preview.height,
            video_fps=preview.fps,
            video_codec=preview.codec,
            video_bitrate=preview.bitrate,
            video_headers=headers,
            formats=[fmt for fmt in formats if fmt is not preview],
            duration=number((feed.get("video") or {}).get("duration")) / 1000,
            cover_url=cover_url,
            title=feed.get("feed_desc_withat") or feed.get("feed_desc") or "",
            author=VideoAuthor(
                uid=poster.get("id") or feed.get("poster_id") or "",
                name=poster.get("nick") or "",
                avatar=poster.get("avatar") or "",
            ),
        )
