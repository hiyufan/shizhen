"""今日头条：微头条取图（手机版页面的 RENDER_DATA），视频交给 yt-dlp，文章明确说不支持。不碰网络。"""

import asyncio
import json
from urllib.parse import quote

import httpx
import pytest

from qiuyuantu.parser import VideoSource, detect_source, parse_video_share_url
from qiuyuantu.parser import base as parser_base
from qiuyuantu.parser.base import VideoInfo
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.ytdlp import YtDlp

POST = "1881280705161283"
IMG = "https://p3-sign.toutiaoimg.com/tos-cn-i-x/{0}~tplv-shrink:1280:2773.jpeg?x-signature=s{0}"


def page(thread_base: dict | None) -> str:
    data = {"articleInfo": {"gid": POST, "thread": {"threadBase": thread_base} if thread_base is not None else None}}
    encoded = quote(json.dumps(data, ensure_ascii=False))
    return f'<html><script id="RENDER_DATA" type="application/json">{encoded}</script></html>'


POST_BASE = {
    "title": "超高清手机壁纸，无水印免费下载",
    "content": "超高清手机壁纸，无水印免费下载",
    "largeImageList": [
        {"url": IMG.format("a"), "urlList": [{"url": IMG.format("a")}]},
        {"url": "", "urlList": [{"url": IMG.format("b")}]},
    ],
    "thumbImageList": [{"url": "https://p3-sign.toutiaoimg.com/thumb.jpeg"}],
    "user": {
        "info": {"userId": 1240729732786071, "name": "末初美图", "avatarUrl": "https://p3-sign.toutiaoimg.com/a.image"}
    },
}


@pytest.fixture
def http(monkeypatch):
    """routes：地址前缀 -> (状态码, 正文, 响应头)；返回记下的请求。"""
    calls = []

    def install(routes):
        class Client:
            def __init__(self, **kw):
                self.follow = kw.get("follow_redirects")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def request(self, method, url, **kw):
                calls.append((method, url, self.follow, kw.get("headers") or {}))
                for prefix, (status, body, headers) in routes.items():
                    if url.startswith(prefix):
                        return httpx.Response(status, text=body, headers=headers, request=httpx.Request(method, url))
                raise AssertionError(f"没有配置的地址：{url}")

            async def get(self, url, **kw):
                return await self.request("GET", url, **kw)

        monkeypatch.setattr(parser_base, "create_async_client", Client)
        return calls

    return install


def test_toutiao_hosts_are_routed_to_the_toutiao_parser():
    for link in (
        "https://m.toutiao.com/is/4L87OofBqBQ/",
        f"https://www.toutiao.com/w/{POST}/",
        "https://toutiao.com/video/1/",
    ):
        assert detect_source(link) == VideoSource.TouTiao, link


def test_short_link_resolves_to_the_post_and_returns_every_large_image(http):
    landing = f"https://m.toutiao.com/w/{POST}/?share_token=secret&utm_source=copy_link"
    calls = http(
        {
            "https://m.toutiao.com/is/": (302, "", {"location": landing}),
            f"https://m.toutiao.com/w/{POST}/": (200, page(POST_BASE), {}),
        }
    )
    info = asyncio.run(parse_video_share_url("https://m.toutiao.com/is/4L87OofBqBQ/"))
    assert info.source == "toutiao" and info.title == "超高清手机壁纸，无水印免费下载"
    assert [i.url for i in info.images] == [IMG.format("a"), IMG.format("b")] and info.cover_url == IMG.format("a")
    assert (info.author.uid, info.author.name) == ("1240729732786071", "末初美图")
    # 短链只读 Location 不跟跳转；作品页按 ID 请求手机版，不带分享参数
    assert calls[0][2] is False and calls[1][1] == f"https://m.toutiao.com/w/{POST}/"
    assert info.page_url == f"https://m.toutiao.com/w/{POST}/"


def test_mobile_video_link_is_handed_to_ytdlp_with_the_desktop_address(http, monkeypatch):
    seen = []

    async def fake_ytdlp(self, url):
        seen.append(url)
        return VideoInfo(video_url="https://v3-web.toutiaovod.com/1.mp4", cover_url="", source="ytdlp")

    monkeypatch.setattr(YtDlp, "parse_share_url", fake_ytdlp)
    http({"https://m.toutiao.com/is/": (302, "", {"location": "https://m.toutiao.com/video/7505382061495176511/?x=1"})})
    info = asyncio.run(parse_video_share_url("https://m.toutiao.com/is/abc/"))
    assert seen == ["https://www.toutiao.com/video/7505382061495176511/"] and info.source == "toutiao"


@pytest.mark.parametrize(
    ("thread_base", "reason", "words"),
    [
        (None, "deleted", "已删除"),
        ({**POST_BASE, "largeImageList": [], "thumbImageList": [], "videoGroup": {"vid": "v1"}}, "unsupported", "视频"),
        ({**POST_BASE, "largeImageList": [], "thumbImageList": []}, "empty", "没有图片"),
    ],
)
def test_posts_without_images_explain_why(http, thread_base, reason, words):
    http({f"https://m.toutiao.com/w/{POST}/": (200, page(thread_base), {})})
    with pytest.raises(ParseError) as caught:
        asyncio.run(parse_video_share_url(f"https://www.toutiao.com/w/{POST}/"))
    assert caught.value.reason == reason and words in str(caught.value)


def test_page_without_data_and_articles_are_reported_clearly(http):
    http({f"https://m.toutiao.com/w/{POST}/": (200, "<html>验证</html>", {})})
    with pytest.raises(ParseError) as caught:
        asyncio.run(parse_video_share_url(f"https://m.toutiao.com/w/{POST}/"))
    assert caught.value.reason == "parse"
    with pytest.raises(ParseError) as caught:
        asyncio.run(parse_video_share_url("https://m.toutiao.com/article/7300000000000000000/"))
    assert caught.value.reason == "unsupported" and "头条文章暂不支持" in str(caught.value)
