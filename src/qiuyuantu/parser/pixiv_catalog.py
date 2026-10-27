"""Pixiv 有限分页目录；只返回作品引用，不在解析时递归批量下载。"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from .base import ContentEntry, TextDocument, VideoAuthor, VideoInfo
from .errors import ParseError
from .pixiv_links import ROOT, PixivLink, page_link, safe_media

PAGE_SIZE = 30


def entry(ident, kind: str = "artwork", row: dict | None = None) -> ContentEntry:
    row = row or {}
    ident = str(ident)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", ident):
        raise ParseError("parse", "目录包含无效作品编号")
    paths = {
        "artwork": "/artworks/",
        "novel": "/novel/show.php?id=",
        "collection": "/collections/",
        "novel_series": "/novel/series/",
    }
    available = (
        row.get("available", True) is not False
        and str(row.get("xRestrict", row.get("x_restrict", 0))) == "0"
        and not row.get("isMasked")
    )
    return ContentEntry(
        ROOT + paths[kind] + ident, str(row.get("title") or ident), safe_media(row.get("url")), kind, available
    )


def novel_references(content: str) -> list[ContentEntry]:
    identifiers = dict.fromkeys(re.findall(r"\[pixivimage:([0-9]+)(?:-[0-9]+)?\]", content))
    return [entry(ident) for ident in list(identifiers)[:500]]


class PixivCatalog:
    async def catalog(self, link: PixivLink, url: str) -> VideoInfo:
        handlers = {
            "user": self.user_catalog,
            "manga_series": self.manga_series,
            "novel_series": self.novel_series,
            "collection": self.collection,
            "search": self.search_catalog,
            "ranking": self.ranking,
        }
        handler = handlers.get(link.kind)
        if not handler:
            raise ParseError("unsupported")
        info = await handler(link)
        info.content_kind = "directory"
        info.page_url = page_link(url, link.page)
        if info.next_page_url:
            info.next_page_url = page_link(url, link.page + 1)
        return info

    async def user_catalog(self, link: PixivLink) -> VideoInfo:
        body = await self.api("/ajax/user/" + link.ident + "/profile/all")
        fields = {
            "artworks": ("illusts", "manga"),
            "illustrations": ("illusts",),
            "manga": ("manga",),
            "novels": ("novels",),
            "collections": ("collectionIds",),
        }
        ids = set()
        for key in fields.get(link.section, ("illusts", "manga")):
            ids.update(str(ident) for ident in (body.get(key) or []))
        if len(ids) > 100_000 or any(not ident.isdecimal() for ident in ids):
            raise ParseError("too_large", "作者目录超过范围或包含无效编号")
        ordered = sorted(ids, key=int, reverse=True)
        start = (link.page - 1) * PAGE_SIZE
        kind = {"novels": "novel", "collections": "collection"}.get(link.section, "artwork")
        return VideoInfo(
            "",
            "",
            title="Pixiv · " + link.ident,
            author=VideoAuthor(uid=link.ident),
            entries=[entry(ident, kind) for ident in ordered[start : start + PAGE_SIZE]],
            entry_total=len(ordered),
            next_page_url="next" if start + PAGE_SIZE < len(ordered) else "",
            notice="目录按作品编号排列；选择一篇作品继续解析，下一页不会自动下载。",
        )

    async def manga_series(self, link: PixivLink) -> VideoInfo:
        body = await self.api("/ajax/series/" + link.ident, params={"p": link.page})
        series = next((row for row in body.get("illustSeries", []) if str(row.get("id")) == link.ident), None)
        page = body.get("page") or {}
        if not series or str(series.get("userId")) != link.owner or str(page.get("seriesId")) != link.ident:
            raise ParseError("parse", "系列或作者身份不匹配")
        thumbs = {str(row["id"]): row for row in (body.get("thumbnails") or {}).get("illust", [])}
        rows = page.get("series") or []
        total = int(page.get("total") or 0)
        return VideoInfo(
            "",
            "",
            title=str(series.get("title") or link.ident),
            entries=[entry(row["workId"], row=thumbs.get(str(row["workId"]))) for row in rows],
            entry_total=total,
            next_page_url="next" if rows and link.page * 12 < total else "",
        )

    async def novel_series(self, link: PixivLink) -> VideoInfo:
        body = await self.api("/ajax/novel/series/" + link.ident)
        if str(body.get("id")) != link.ident or str(body.get("xRestrict", 0)) != "0":
            raise ParseError("restricted", "小说系列无法匿名读取")
        rows = await self.api("/ajax/novel/series/" + link.ident + "/content_titles")
        start = (link.page - 1) * PAGE_SIZE
        return VideoInfo(
            "",
            "",
            title=str(body.get("title") or link.ident),
            entries=[entry(row["id"], "novel", row) for row in rows[start : start + PAGE_SIZE]],
            entry_total=len(rows),
            next_page_url="next" if start + PAGE_SIZE < len(rows) else "",
        )

    async def collection(self, link: PixivLink) -> VideoInfo:
        body = await self.api("/ajax/collection/" + link.ident)
        thumbnails = body.get("thumbnails") or {}
        item = next((row for row in thumbnails.get("collection", []) if str(row.get("id")) == link.ident), None)
        if not item or str(item.get("xRestrict", 0)) != "0":
            raise ParseError("restricted", "集合身份或访问权限不匹配")
        tiles = (body.get("data") or {}).get("detail", {}).get("tiles") or []
        start = (link.page - 1) * PAGE_SIZE
        info = VideoInfo(
            "",
            safe_media(item.get("thumbnailImageUrl")),
            title=str(item.get("title") or link.ident),
            entry_total=len(tiles),
            next_page_url="next" if start + PAGE_SIZE < len(tiles) else "",
            notice="集合中的作品需要逐项解析；外部链接只提供原页面入口。",
        )
        for tile in tiles[start : start + PAGE_SIZE]:
            self._collection_tile(info, tile, thumbnails)
        return info

    def _collection_tile(self, info: VideoInfo, tile: dict, thumbnails: dict) -> None:
        if tile.get("status") != "Active":
            return
        if tile.get("type") == "Work":
            kinds = {
                "illust": ("artwork", "illust"),
                "novel": ("novel", "novel"),
                "novelSeries": ("novel_series", "novelSeries"),
            }
            if target := kinds.get(tile.get("workType")):
                ident = str(tile.get("workId"))
                row = next((row for row in thumbnails.get(target[1], []) if str(row.get("id")) == ident), {})
                info.entries.append(entry(ident, target[0], row))
        elif tile.get("type") == "Text" and tile.get("text"):
            info.documents.append(TextDocument(info.title, str(tile["text"])[:50_000]))
        elif tile.get("type") in {"ExternalUrl", "Link"}:
            url = tile.get("url", "")
            p = urlparse(url)
            if p.scheme in {"http", "https"} and p.hostname and not p.username and not p.port:
                info.entries.append(ContentEntry(url, str(tile.get("title") or p.hostname), kind="external"))

    async def search_catalog(self, link: PixivLink) -> VideoInfo:
        if len(link.ident) > 100:
            raise ParseError("unsupported", "标签过长")
        params = {
            "word": link.ident,
            "order": "date_d",
            "mode": "safe",
            "s_mode": "s_tag",
            "type": "all",
            **link.params(),
            "p": link.page,
        }
        if params["mode"] not in {"safe", "all"} or params["order"] not in {"date_d", "date"}:
            raise ParseError("restricted", "仅支持公开安全模式与日期排序")
        params["mode"] = "safe"
        return await self._search_page(link, params)

    async def _search_page(self, link: PixivLink, params: dict) -> VideoInfo:
        if link.section == "collections":
            body = await self.api(
                "/ajax/collections/search",
                params={
                    "tags[]": link.ident,
                    "mode": "safe",
                    "limit": PAGE_SIZE,
                    "offset": (link.page - 1) * PAGE_SIZE,
                },
            )
            rows = (body.get("thumbnails") or {}).get("collection", [])
            kind, total = "collection", 0
        else:
            section = {
                "novels": "novels",
                "manga": "manga",
                "illustrations": "illustrations",
                "ugoira": "illustrations",
            }.get(link.section, "artworks")
            if link.section in {"illustrations", "ugoira"}:
                params["type"] = "ugoira" if link.section == "ugoira" else "illust"
            body = await self.api("/ajax/search/" + section + "/" + quote(link.ident, safe=""), params=params)
            key = "novel" if section == "novels" else "illustManga"
            result = body.get(key) or body.get("illust") or body.get("manga") or {}
            rows, total = result.get("data") or [], int(result.get("total") or 0)
            kind = "novel" if section == "novels" else "artwork"
        size = 30 if kind in {"collection", "novel"} else 60
        more = bool(rows) and (not total or link.page * size < total)
        rows = [row for row in rows if isinstance(row, dict) and str(row.get("id", "")).isdecimal()]
        return VideoInfo(
            "",
            "",
            title="Pixiv · " + link.ident,
            entries=[entry(row["id"], kind, row) for row in rows],
            entry_total=total,
            next_page_url="next" if more else "",
        )

    async def ranking(self, link: PixivLink) -> VideoInfo:
        params = {"mode": "daily", **link.params(), "p": link.page}
        if "r18" in params["mode"]:
            raise ParseError("restricted", "这个榜单无法匿名读取")
        if link.section == "novels":
            body = await self.api("/ajax/ranking/novel", params=params)
            rows = (body.get("display_a") or {}).get("rank_a") or []
            entries = [entry(row["id"], "novel", row) for row in rows]
            more = bool(rows and int(body.get("end") or 0) < 500)
        else:
            body = await self.get_json(ROOT + "/ranking.php", params={**params, "format": "json"})
            rows = body.get("contents") or []
            entries = [entry(row["illust_id"], row=row) for row in rows]
            more = bool(body.get("next"))
        return VideoInfo(
            "", "", title="Pixiv · " + params["mode"], entries=entries, next_page_url="next" if more else ""
        )
