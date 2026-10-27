"""匿名读取 Pixiv 公开作品；目录与纯文本保持独立结果语义。"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from .base import BaseParser, FormatInfo, ImgInfo, MediaAsset, TextDocument, VideoAuthor, VideoInfo
from .errors import ParseError
from .pixiv_catalog import PixivCatalog
from .pixiv_links import ROOT, PixivLink, classify_link, safe_media


class Pixiv(PixivCatalog, BaseParser):
    def get_default_headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua("windows"), "Referer": ROOT + "/"}

    async def api(self, path: str, **kw):
        data = await self.get_json(ROOT + path, **kw)
        if not isinstance(data, dict) or data.get("error") is not False:
            raise ParseError("restricted", "Pixiv 未提供这条公开内容")
        return data.get("body")

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        link = classify_link(share_url)
        if link.kind == "redirect":
            target = urljoin(share_url, await self.redirect_target(share_url))
            link = classify_link(target)
            if link.kind != "user":
                raise ParseError("unsupported", "作者短链没有返回公开主页")
            share_url = target
        if link.kind == "file":
            return await self._file(share_url)
        if link.kind in {"artwork", "unlisted"}:
            return await self.artwork(link)
        if link.kind == "novel":
            return await self.novel(link.ident)
        return await self.catalog(link, share_url)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(ROOT + "/artworks/" + video_id)

    async def _file(self, url: str) -> VideoInfo:
        path = urlparse(url).path
        if match := re.search(r"/([0-9]+)_ugoira[0-9]+x[0-9]+\.zip$", path):
            info = await self.parse_video_id(match[1])
            if not any(asset.url == url for asset in info.assets):
                raise ParseError("unsupported", "帧包与当前作品不匹配，请复制作品链接")
            return info
        if not safe_media(url) or not re.search(r"\.(?:jpe?g|png|gif|webp)$", path, re.I):
            raise ParseError("unsupported", "请复制 Pixiv 作品链接")
        return VideoInfo(
            "",
            url,
            title="Pixiv",
            images=[ImgInfo(url)],
            page_url=url,
            notice="这是单个图片文件；需要整篇作品时，请复制作品链接。",
        )

    async def artwork(self, link: PixivLink) -> VideoInfo:
        suffix = "unlisted/" if link.kind == "unlisted" else ""
        body = await self.api("/ajax/illust/" + suffix + link.ident)
        ident = str(body.get("illustId") or body.get("id") or "")
        if not ident.isdecimal() or (link.kind == "artwork" and ident != link.ident):
            raise ParseError("parse", "作品身份不匹配")
        require_public(body)
        original = safe_media((body.get("urls") or {}).get("original"))
        if not original:
            raise ParseError("restricted", "Pixiv 没有提供原始图片")
        page = ROOT + "/artworks/" + ("unlisted/" + link.ident if suffix else ident)
        info = VideoInfo(
            "",
            safe_media((body.get("urls") or {}).get("regular")) or original,
            title=str(body.get("title") or ident),
            author=author(body),
            page_url=page,
        )
        if body.get("illustType") == 2:
            return await self._animation(info, ident, suffix + link.ident)
        count = body.get("pageCount")
        if type(count) is not int or not 1 <= count <= 500:
            raise ParseError("too_large", "图集超过 500 页或页数无效")
        pages = (
            [{"urls": {"original": original}}]
            if count == 1
            else await self.api("/ajax/illust/" + suffix + link.ident + "/pages")
        )
        if not isinstance(pages, list) or len(pages) != count:
            raise ParseError("parse", "图集页数不完整")
        info.images = [ImgInfo(safe_media((row.get("urls") or {}).get("original"))) for row in pages]
        if not all(image.url and _image_identity(image.url, ident) for image in info.images):
            raise ParseError("parse", "图集图片与作品不匹配")
        return info

    async def _animation(self, info: VideoInfo, ident: str, endpoint: str) -> VideoInfo:
        body = await self.api("/ajax/illust/" + endpoint + "/ugoira_meta")
        frames = animation_frames(body.get("frames"))
        urls = list(dict.fromkeys(body.get(key) for key in ("originalSrc", "src") if body.get(key)))
        for index, url in enumerate(urls):
            if not safe_media(url) or not re.search(rf"/{ident}_ugoira[0-9]+x[0-9]+\.zip$", urlparse(url).path):
                raise ParseError("parse", "动画帧包与作品不匹配")
            info.assets.append(MediaAsset(url, "帧包" if index == 0 else "预览帧包", "archive", info.cover_url, "zip"))
        if not urls:
            raise ParseError("restricted", "Pixiv 没有提供动画帧包")
        info.animation_frames = frames
        info.duration = sum(frame["delay"] for frame in frames) / 1000
        if len(frames) <= 600 and info.duration <= 30:
            info.formats = [FormatInfo(label="MP4", format_spec="pixiv-ugoira", ext="mp4")]
        info.notice = "帧包与时序文件一起保存才能还原动画；帧包尺寸可能小于独立原始图片。"
        if not info.formats:
            info.notice = "这段动画超过转换限制，可以下载帧包与时序文件。"
        return info

    async def novel(self, ident: str) -> VideoInfo:
        body = await self.api("/ajax/novel/" + ident)
        if str(body.get("id")) != ident:
            raise ParseError("parse", "小说身份不匹配")
        require_public(body)
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ParseError("restricted", "Pixiv 未提供小说正文")
        if len(content.encode("utf-8")) > 1_000_000:
            raise ParseError("too_large", "小说正文超过 1 MB")
        page = ROOT + "/novel/show.php?id=" + ident
        title = str(body.get("title") or ident)
        info = VideoInfo(
            "",
            safe_media(body.get("coverUrl")),
            title=title,
            author=author(body),
            page_url=page,
            documents=[TextDocument(title, content)],
            content_kind="document",
            notice="TXT 保留 Pixiv 原文标记；封面、上传插图与引用作品分别列出。",
        )
        if info.cover_url:
            info.assets.append(MediaAsset(info.cover_url, "封面"))
        embedded = body.get("textEmbeddedImages") or {}
        for index, image in enumerate(embedded.values()):
            if url := safe_media((image.get("urls") or {}).get("original")):
                info.assets.append(MediaAsset(url, str(index + 1)))
        from .pixiv_catalog import novel_references

        info.entries = novel_references(content)
        return info


def require_public(body: dict) -> None:
    if str(body.get("xRestrict", 0)) != "0" or str(body.get("restrict", 0)) != "0":
        raise ParseError("restricted", "此内容无法匿名读取")


def author(body: dict) -> VideoAuthor:
    return VideoAuthor(str(body.get("userId") or ""), str(body.get("userName") or ""))


def _image_identity(url: str, ident: str) -> bool:
    return bool(
        re.match(
            re.escape(ident) + r"(?:-[a-zA-Z0-9]+)?_(?:p[0-9]+|ugoira[0-9]+)\.", urlparse(url).path.rsplit("/", 1)[-1]
        )
    )


def animation_frames(rows) -> list[dict]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10_000:
        raise ParseError("too_large", "动画帧数超过限制")
    frames = []
    for row in rows:
        if not isinstance(row, dict):
            raise ParseError("parse", "动画帧列表无效")
        name, delay = row.get("file"), row.get("delay")
        if not isinstance(name, str) or not re.fullmatch(r"[0-9]{6}\.(?:jpg|jpeg|png|gif)", name):
            raise ParseError("parse", "动画帧文件名无效")
        if type(delay) is not int or not 0 < delay <= 60_000:
            raise ParseError("parse", "动画帧时长无效")
        frames.append({"file": name, "delay": delay})
    if len({row["file"] for row in frames}) != len(frames) or sum(row["delay"] for row in frames) > 600_000:
        raise ParseError("too_large", "动画超过 10 分钟或帧列表重复")
    return frames
