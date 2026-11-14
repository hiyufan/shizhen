"""Threads 帖子：从手机版帖子页的内嵌 JSON 取图片与视频；分享短链先跟跳转。不碰网络。"""

import json

import httpx
import pytest

from qiuyuantu.parser import VideoSource, base, detect_source, threads
from qiuyuantu.parser.errors import ParseError

CODE = "Dfbw28jkon0"
VIDEO = "https://scontent-lax3-2.cdninstagram.com/o1/v/t16/f2/m84/video.mp4"
IMAGE = "https://scontent-lax3-1.cdninstagram.com/v/t51/1440.jpg"
SMALL = "https://scontent-lax3-1.cdninstagram.com/v/t51/320.jpg"


def post(code=CODE, **kw):
    return {
        "code": code,
        "media_type": 2,
        "caption": {"text": " 我跌跌撞撞奔向你 "},
        "user": {"pk": "7", "username": "huiunyz", "profile_pic_url": "https://scontent.cdninstagram.com/a.jpg"},
        "image_versions2": {"candidates": [{"url": SMALL, "width": 320, "height": 568}]},
        "video_versions": [{"type": 101, "url": VIDEO}],
        **kw,
    }


def page(*posts):
    data = {"require": [["ScheduledServerJS", {"__bbox": {"result": {"data": {"media": p}}}}] for p in posts]}
    return f'<script type="application/json" data-sjs>{json.dumps(data)}</script>'


@pytest.fixture
def site(monkeypatch):
    routes, seen = {}, []

    def handle(request):
        seen.append(request)
        reply = routes.get(str(request.url).split("?")[0])
        if reply is None:
            return httpx.Response(200, text="<title>Threads</title>")
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, text=reply)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    return routes, seen


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.threads.com/@huiunyz/post/{CODE}?xmt=abc&slof=1",
        f"https://www.threads.net/@huiunyz/post/{CODE}",
        f"https://www.threads.com/@huiunyz/post/{CODE}/media",
        f"https://www.threads.com/t/{CODE}",
    ],
)
async def test_post_link_returns_video_and_author(site, url):
    routes, seen = site
    # 回复和推荐帖也在页面里，按 code 认出这一条
    routes[f"https://www.threads.com/t/{CODE}"] = page(post("OtherCode1", video_versions=[{"url": SMALL}]), post())
    info = await threads.Threads().parse_share_url(url)
    assert (info.video_url, info.source, info.title) == (VIDEO, "threads", "我跌跌撞撞奔向你")
    assert info.author.name == "huiunyz" and info.page_url == f"https://www.threads.com/@huiunyz/post/{CODE}"
    assert "iPhone" in seen[-1].headers["user-agent"] and "cookie" not in seen[-1].headers
    assert detect_source(url) == VideoSource.Threads


async def test_carousel_keeps_largest_image_of_each_item(site):
    routes, _ = site
    items = [
        {
            "media_type": 1,
            "image_versions2": {
                "candidates": [
                    {"url": SMALL, "width": 320, "height": 568},
                    {"url": IMAGE, "width": 1440, "height": 2560},
                ]
            },
        },
        {"media_type": 1, "image_versions2": {"candidates": [{"url": IMAGE + "?2", "width": 1440, "height": 2560}]}},
    ]
    routes[f"https://www.threads.com/t/{CODE}"] = page(post(media_type=8, carousel_media=items, video_versions=None))
    info = await threads.Threads().parse_video_id(CODE)
    assert [image.url for image in info.images] == [IMAGE, IMAGE + "?2"] and not info.video_url


async def test_share_link_follows_redirect_to_the_post(site):
    routes, seen = site
    routes["https://www.threads.com/share/_qnW5t1Du"] = httpx.Response(
        302, headers={"location": f"https://www.threads.com/@huiunyz/post/{CODE}?xmt=1"}
    )
    routes[f"https://www.threads.com/t/{CODE}"] = page(post())
    info = await threads.Threads().parse_share_url("https://www.threads.com/share/_qnW5t1Du")
    assert info.video_url == VIDEO and str(seen[0].url) == "https://www.threads.com/share/_qnW5t1Du"


@pytest.mark.parametrize(
    ("url", "reply", "reason"),
    [
        ("https://www.threads.com/@zuck", None, "unsupported"),
        ("https://www.threads.com/share/_qnW5t1Du", httpx.Response(200, text="<title>Threads</title>"), "unsupported"),
        (f"https://www.threads.com/t/{CODE}", None, "restricted"),
        (
            f"https://www.threads.com/t/{CODE}",
            page(post(media_type=19, video_versions=None, image_versions2=None)),
            "empty",
        ),
    ],
)
async def test_pages_without_post_media_explain_why(site, url, reply, reason):
    routes, _ = site
    if reply is not None:
        routes[url.split("?")[0]] = reply
    with pytest.raises(ParseError) as caught:
        await threads.Threads().parse_share_url(url)
    assert caught.value.reason == reason


def quoted_post(quoted, **share):
    return post(
        media_type=19,
        video_versions=[],
        image_versions2={"candidates": []},
        text_post_app_info={"share_info": {"quoted_attachment_post": quoted, **share}},
    )


async def test_issue_11_share_keeps_quoted_video_and_original_author(site):
    routes, seen = site
    original = post(
        "DfuHsp2DSNR",
        caption={"text": "原帖视频"},
        user={"pk": "8", "username": "original.author", "profile_pic_url": IMAGE},
    )
    target = quoted_post(original)
    routes["https://www.threads.com/share/_tDtEFsQq"] = httpx.Response(
        302, headers={"location": f"https://www.threads.com/@huiunyz/post/{CODE}?xmt=1"}
    )
    routes[f"https://www.threads.com/t/{CODE}"] = page(
        post("Recommendation1", video_versions=[{"url": "https://cdn.example/wrong.mp4"}]),
        target,
    )
    info = await threads.Threads().parse_share_url("https://www.threads.com/share/_tDtEFsQq")
    assert info.video_url == VIDEO and info.source == "threads" and info.title == "原帖视频"
    assert (info.author.uid, info.author.name, info.author.avatar) == ("8", "original.author", IMAGE)
    assert info.page_url == f"https://www.threads.com/@huiunyz/post/{CODE}"
    assert "引用的原帖" in info.notice and len(seen) == 2


@pytest.mark.parametrize("kind", ["image", "mixed", "videos"])
async def test_quoted_photo_and_mixed_carousels_keep_all_media(site, kind):
    routes, _ = site
    image = post("Quoted1", media_type=1, video_versions=[])
    video = post("Quoted2")
    if kind == "image":
        original = image
    else:
        items = [image, video, video] if kind == "mixed" else [video, video]
        original = post("Quoted3", media_type=8, carousel_media=items)
    routes[f"https://www.threads.com/t/{CODE}"] = page(quoted_post(original))
    info = await threads.Threads().parse_video_id(CODE)
    assert len(info.images) == (0 if kind == "videos" else 1)
    assert len(info.assets) == (0 if kind == "image" else 2)
    assert not info.video_url and "引用的原帖" in info.notice


async def test_posts_own_media_takes_priority_over_quoted_attachment(site):
    routes, _ = site
    own = post(
        text_post_app_info={
            "share_info": {
                "quoted_attachment_post": post("Quoted1", video_versions=[{"url": "https://cdn.example/quoted.mp4"}]),
                "quoted_attachment_post_unavailable": True,
            }
        }
    )
    routes[f"https://www.threads.com/t/{CODE}"] = page(own)
    info = await threads.Threads().parse_video_id(CODE)
    assert info.video_url == VIDEO and not info.notice and info.author.name == "huiunyz"


async def test_unavailable_quoted_post_does_not_use_cached_media_or_recommendations(site):
    routes, seen = site
    target = quoted_post(post("Quoted1"), quoted_attachment_post_unavailable=True)
    routes[f"https://www.threads.com/t/{CODE}"] = page(target, post("Recommendation1"))
    with pytest.raises(ParseError) as caught:
        await threads.Threads().parse_video_id(CODE)
    assert caught.value.reason == "restricted" and "引用的原帖" in caught.value.detail and len(seen) == 1


@pytest.mark.parametrize(
    "app_info", [None, [], "invalid", {"share_info": []}, {"share_info": {"quoted_attachment_post": []}}]
)
async def test_malformed_quote_data_is_not_treated_as_media(site, app_info):
    routes, _ = site
    target = quoted_post(None)
    target["text_post_app_info"] = app_info
    routes[f"https://www.threads.com/t/{CODE}"] = page(target, post("Recommendation1"))
    with pytest.raises(ParseError) as caught:
        await threads.Threads().parse_video_id(CODE)
    assert caught.value.reason == "empty"


async def test_reply_and_link_preview_are_not_a_quote_fallback(site):
    routes, _ = site
    target = quoted_post(None)
    target["replies"] = [post("Reply1")]
    target["text_post_app_info"]["link_preview_attachment"] = post("Preview1")
    routes[f"https://www.threads.com/t/{CODE}"] = page(target, post("Recommendation1"))
    with pytest.raises(ParseError) as caught:
        await threads.Threads().parse_video_id(CODE)
    assert caught.value.reason == "empty"


@pytest.mark.parametrize(("depth", "allowed"), [(3, True), (4, False)])
async def test_nested_quote_traversal_has_a_fixed_limit(site, depth, allowed):
    routes, seen = site
    target = post("QuotedVideo")
    for _ in range(depth):
        target = quoted_post(target)
    routes[f"https://www.threads.com/t/{CODE}"] = page(target)
    if allowed:
        info = await threads.Threads().parse_video_id(CODE)
        assert info.video_url == VIDEO and info.notice
    else:
        with pytest.raises(ParseError) as caught:
            await threads.Threads().parse_video_id(CODE)
        assert caught.value.reason == "unsupported" and "引用层级" in caught.value.detail
    assert len(seen) == 1
