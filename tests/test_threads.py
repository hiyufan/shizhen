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
