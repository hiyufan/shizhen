"""匿名 Pixiv：身份、分页、混合结果及下载签名契约。"""

import dataclasses

import httpx
import pytest

from qiuyuantu.parser import base, detect_source, parse_video_share_url
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.pixiv_links import classify_link
from qiuyuantu.web.parse import _client_data

ROOT = "https://www.pixiv.net"
IMAGE = "https://i.pximg.net/img-original/img/2026/01/01/00/00/00/42_p0.png"
ZIP = "https://i.pximg.net/img-zip-ugoira/img/2026/01/01/00/00/00/42_ugoira1920x1080.zip"
FRAMES = [{"file": "000000.jpg", "delay": 120}, {"file": "000001.jpg", "delay": 130}]


@pytest.fixture
def public_http(monkeypatch):
    routes, calls = {}, []

    def handle(request):
        calls.append(request)
        data = routes[request.url.path]
        if callable(data):
            data = data(request)
        return httpx.Response(200, json={"error": False, "body": data})

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    return routes, calls


def artwork(**kw):
    return {
        "id": "42",
        "illustType": 0,
        "pageCount": 1,
        "xRestrict": 0,
        "restrict": 0,
        "title": "Example",
        "urls": {"original": IMAGE},
        **kw,
    }


@pytest.mark.parametrize(
    "path,kind,ident",
    [
        ("/en/artworks/42", "artwork", "42"),
        ("/i/42", "artwork", "42"),
        ("/member_illust.php?mode=medium&illust_id=42", "artwork", "42"),
        ("/u/42", "user", "42"),
        ("/users/42/novels?p=2", "user", "42"),
        ("/novel/show.php?id=42", "novel", "42"),
        ("/n/42", "novel", "42"),
        ("/novel/series/42", "novel_series", "42"),
        ("/user/7/series/42", "manga_series", "42"),
        ("/collections/79068454229757002471", "collection", "79068454229757002471"),
        ("/artworks/unlisted/abcdefghijkl", "unlisted", "abcdefghijkl"),
    ],
)
def test_public_url_types(path, kind, ident):
    link = classify_link(ROOT + path)
    assert (link.kind, link.ident) == (kind, ident)


@pytest.mark.parametrize(
    "url",
    [
        "https://pixiv.net.evil.example/artworks/42",
        "https://user@www.pixiv.net/artworks/42",
        "https://www.pixiv.net:444/artworks/42",
        ROOT + "/users/42?p=0",
    ],
)
def test_untrusted_links_are_rejected(url):
    with pytest.raises(ParseError):
        classify_link(url)


async def test_original_pages_keep_order_and_signatures_without_credentials(public_http):
    routes, calls = public_http
    routes["/ajax/illust/42"] = artwork(illustType=1, pageCount=2)
    second = IMAGE.replace("_p0", "_p1")
    routes["/ajax/illust/42/pages"] = [{"urls": {"original": url}} for url in (IMAGE, second)]
    info = await parse_video_share_url(ROOT + "/i/42")
    data = _client_data(info, ROOT + "/i/42")
    assert [row["url"] for row in data["images"]] == [IMAGE, second]
    assert IMAGE in data["sig"] and second in data["sig"]
    assert data["source"] == "pixiv" and not data["entries"] and not data["documents"]
    assert all("cookie" not in call.headers and "authorization" not in call.headers for call in calls)
    assert all(call.headers["referer"] == ROOT + "/" for call in calls)


@pytest.mark.parametrize(
    "changes",
    [{"id": "99"}, {"xRestrict": 1}, {"restrict": 1}, {"urls": {"original": "https://evil.example/42_p0.png"}}],
)
async def test_wrong_identity_or_restricted_work_is_not_success(public_http, changes):
    routes, _ = public_http
    routes["/ajax/illust/42"] = artwork(**changes)
    with pytest.raises(ParseError):
        await parse_video_share_url(ROOT + "/artworks/42")


async def test_ugoira_is_server_composed_format_and_independent_zip(public_http):
    routes, _ = public_http
    routes["/ajax/illust/42"] = artwork(illustType=2)
    routes["/ajax/illust/42/ugoira_meta"] = {"originalSrc": ZIP, "src": ZIP, "frames": FRAMES}
    info = await parse_video_share_url(ROOT + "/artworks/42")
    assert info.video_url == "" and not info.images
    assert info.assets[0].url == ZIP and info.assets[0].ext == "zip"
    assert info.formats[0].format_spec == "pixiv-ugoira" and not info.formats[0].url
    assert info.animation_frames == FRAMES and info.duration == 0.25


async def test_author_pages_do_not_recursively_fetch_works(public_http):
    routes, calls = public_http
    routes["/ajax/user/7/profile/all"] = {"illusts": {str(i): None for i in range(1, 36)}, "manga": {"35": None}}
    first = await parse_video_share_url(ROOT + "/users/7")
    second = await parse_video_share_url(first.next_page_url)
    assert len(first.entries) == 30 and len(second.entries) == 5
    assert first.entries[0].url.endswith("/35") and second.entries[-1].url.endswith("/1")
    assert first.entry_total == 35 and not second.next_page_url
    assert len(calls) == 2


async def test_empty_directory_keeps_explicit_result_kind(public_http):
    routes, _ = public_http
    routes["/ajax/user/7/profile/all"] = {"illusts": {}, "manga": {}}
    info = await parse_video_share_url(ROOT + "/users/7")
    assert info.content_kind == "directory" and not info.entries


async def test_novel_content_stays_plain_text_and_references_are_separate(public_http):
    routes, _ = public_http
    text = "<script>alert(1)</script>\n[newpage]\n[uploadedimage:3]\n[pixivimage:42-1]\n[pixivimage:42-2]"
    routes["/ajax/novel/7"] = {
        "id": "7",
        "xRestrict": 0,
        "title": "Novel",
        "content": text,
        "textEmbeddedImages": {"3": {"urls": {"original": IMAGE}}},
    }
    info = await parse_video_share_url(ROOT + "/n/7")
    assert info.documents[0].content == text and info.content_kind == "document"
    assert info.assets[0].url == IMAGE and len(info.entries) == 1
    assert not info.video_url and not info.formats


async def test_mixed_collection_does_not_follow_external_links(public_http):
    routes, calls = public_http
    ident = "79068454229757002471"
    routes["/ajax/collection/" + ident] = {
        "thumbnails": {
            "collection": [{"id": ident, "title": "Mixed", "xRestrict": 0}],
            "illust": [{"id": "42", "url": IMAGE}],
        },
        "data": {
            "detail": {
                "tiles": [
                    {"type": "Work", "status": "Active", "workType": "illust", "workId": "42"},
                    {"type": "Text", "status": "Active", "text": "<b>Plain</b>"},
                    {"type": "ExternalUrl", "status": "Active", "url": "https://example.org/article"},
                    {"type": "Link", "status": "Active", "url": "javascript:alert(1)"},
                ]
            }
        },
    }
    info = await parse_video_share_url(ROOT + "/collections/" + ident)
    assert len(info.entries) == 2 and info.entries[1].kind == "external"
    assert info.documents[0].content == "<b>Plain</b>" and len(calls) == 1
    data = _client_data(info, info.page_url)
    assert IMAGE in data["sig"] and "https://example.org/article" not in data["sig"]
    assert dataclasses.asdict(info)["entry_total"] == 4


async def test_search_skips_advertisement_rows(public_http):
    routes, _ = public_http
    routes["/ajax/search/artworks/test"] = {
        "illustManga": {"data": [{"isAdContainer": True}, {"id": "42", "title": "Work"}], "total": 100}
    }
    info = await parse_video_share_url(ROOT + "/tags/test/artworks")
    assert len(info.entries) == 1 and info.entries[0].url.endswith("/42")


def test_sketch_is_registered_before_parent_domain():
    assert detect_source("https://sketch.pixiv.net/@name") == VideoSource.PixivSketch
    assert detect_source(ROOT + "/artworks/42") == VideoSource.Pixiv


async def test_long_animation_keeps_original_download_without_conversion(public_http):
    routes, _ = public_http
    routes["/ajax/illust/42"] = artwork(illustType=2)
    frames = [{"file": f"{i:06d}.jpg", "delay": 80} for i in range(601)]
    routes["/ajax/illust/42/ugoira_meta"] = {"originalSrc": ZIP, "frames": frames}
    info = await parse_video_share_url(ROOT + "/artworks/42")
    assert info.assets[0].url == ZIP and len(info.animation_frames) == 601
    assert not info.formats and not info.video_url


async def test_ugoira_tag_uses_animation_filter_instead_of_mixed_search(public_http):
    routes, calls = public_http
    routes["/ajax/search/illustrations/test"] = {"illust": {"data": [{"id": "42", "illustType": 2}], "total": 1}}
    info = await parse_video_share_url(ROOT + "/tags/test/ugoira?mode=all")
    assert len(info.entries) == 1 and not info.next_page_url
    assert calls[0].url.params["type"] == "ugoira" and calls[0].url.params["mode"] == "safe"


async def test_unavailable_series_chapter_stays_visible_without_download_action(public_http):
    routes, _ = public_http
    routes["/ajax/novel/series/7"] = {"id": "7", "xRestrict": 0}
    routes["/ajax/novel/series/7/content_titles"] = [{"id": "42", "title": "Unavailable", "available": False}]
    info = await parse_video_share_url(ROOT + "/novel/series/7")
    assert info.entry_total == 1 and len(info.entries) == 1 and not info.entries[0].available
