"""普通微博视频与 H5 多档视频的选择，不请求真实网络。"""

import json

import httpx
import pytest

from qiuyuantu.parser import base, weibo
from qiuyuantu.parser.base import FormatInfo
from qiuyuantu.parser.quality import best_video_format


@pytest.fixture
def responses(monkeypatch):
    replies = {}

    def response(request):
        key = str(request.url)
        assert key in replies, "未配置的请求：" + key
        reply = replies[key]
        if callable(reply):
            return reply(request)
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(response), **kw)
    )
    return replies


async def test_h5_uses_actual_probed_resolution_instead_of_dictionary_order(responses, monkeypatch):
    replies = responses
    replies["https://weibo.com/tv/api/component?page=%2Ftv%2Fshow%2F1034%3A99"] = {}
    replies["https://h5.video.weibo.com/api/component?page=/show/1034:99"] = {
        "data": {
            "Component_Play_Playinfo": {
                "urls": {"标清 360P": "//cdn.test/low.mp4", "4K": "//cdn.test/high.mp4"},
                "cover_image": "https://cdn.test/cover.jpg",
                "title": "video",
                "user": {"id": 1},
                "author": "author",
                "avatar": "https://cdn.test/avatar.jpg",
            }
        }
    }

    async def probe(url, _headers):
        height = 360 if "low" in url else 1080
        return FormatInfo(url=url, width=height * 16 // 9, height=height, label=f"{height}p")

    monkeypatch.setattr(weibo.mp4_probe, "probe", probe)
    info = await weibo.WeiBo().parse_video_id("1034:99")
    assert info.video_url == "https://cdn.test/high.mp4" and info.height == 1080
    assert best_video_format(info).label == "1080p"
    assert info.cover_url == "https://cdn.test/cover.jpg"


async def test_normal_post_reads_video_playback_list_and_preserves_image_gallery(responses):
    responses["https://weibo.com/ajax/statuses/show?id=post"] = {}
    responses["https://m.weibo.cn/statuses/show?id=post"] = {
        "data": {
            "text": "video and images",
            "pics": [{"original": {"url": "https://cdn.test/original.jpg"}}],
            "page_info": {
                "page_pic": "https://cdn.test/cover.jpg",
                "media_info": {
                    "duration": 5,
                    "playback_list": [
                        {"play_info": {"url": "https://cdn.test/720.mp4", "width": 1280, "height": 720}},
                        {
                            "play_info": {
                                "url": "https://cdn.test/1080.mp4",
                                "width": 1920,
                                "height": 1080,
                                "fps": 60,
                                "bitrate": 8000000,
                                "video_codecs": "avc1",
                            }
                        },
                    ],
                },
            },
        }
    }
    info = await weibo.WeiBo().parse_share_url("https://weibo.com/123/post")
    assert info.video_url == "https://cdn.test/1080.mp4" and info.video_fps == 60
    assert info.duration == 5 and info.images[0].url.endswith("original.jpg")
    assert len(info.formats) == 1 and info.formats[0].height == 720


async def test_desktop_status_keeps_full_playback_tiers_and_original_pictures(responses):
    responses["https://weibo.com/ajax/statuses/show?id=post"] = {
        "page_info": {
            "media_info": {
                "playback_list": [
                    {"play_info": {"url": "https://cdn.test/high.mp4", "width": 3840, "height": 2160}},
                    {
                        "play_info": {
                            "url": "https://cdn.test/scrubber.jpg",
                            "width": 8192,
                            "height": 8192,
                            "mime": "image/jpeg",
                        }
                    },
                ]
            }
        },
        "pic_ids": ["b", "a"],
        "pic_infos": {
            "a": {"original": {"url": "https://cdn.test/a.jpg"}},
            "b": {"original": {"url": "https://cdn.test/b.jpg"}},
        },
    }
    info = await weibo.WeiBo().parse_share_url("https://weibo.com/123/post")
    assert info.height == 2160
    assert [pic.url for pic in info.images] == ["https://cdn.test/b.jpg", "https://cdn.test/a.jpg"]
    assert info.video_headers["Referer"] == "https://weibo.com/"


async def test_tv_link_uses_post_playback_list_instead_of_only_legacy_urls(responses):
    responses["https://weibo.com/tv/api/component?page=%2Ftv%2Fshow%2F1034%3A99"] = {
        "data": {"Component_Play_Playinfo": {"mid": "123", "urls": {"low": "https://cdn.test/low.mp4"}}}
    }
    responses["https://weibo.com/ajax/statuses/show?id=123"] = {
        "page_info": {
            "media_info": {
                "playback_list": [{"play_info": {"url": "https://cdn.test/high.mp4", "width": 1920, "height": 1080}}]
            }
        }
    }
    info = await weibo.WeiBo().parse_video_id("1034:99")
    assert info.video_url == "https://cdn.test/high.mp4" and info.height == 1080


async def test_guest_session_is_scoped_to_each_parse_and_not_sent_to_passport(responses):
    counts = {"guest": 0, "status": 0}

    def status(request):
        if request.headers.get("cookie") == "SUB=anonymous-test":
            counts["status"] += 1
            return httpx.Response(200, json={"pics": [{"original": {"url": "https://cdn.test/raw.jpg"}}]})
        return httpx.Response(302, headers={"Location": "https://passport.weibo.com/visitor/visitor?entry=miniblog"})

    def generation(request):
        assert request.method == "POST" and "cookie" not in request.headers
        counts["guest"] += 1
        return httpx.Response(
            200, text='window.gen_callback && gen_callback({"data":{"tid":"tid","new_tid":true,"confidence":100}});'
        )

    def callback(request):
        assert "cookie" not in request.headers
        return httpx.Response(200, headers={"set-cookie": "SUB=anonymous-test; Domain=.weibo.com; Path=/"})

    responses.update(
        {
            "https://weibo.com/ajax/statuses/show?id=post": status,
            "https://passport.weibo.com/visitor/visitor?entry=miniblog": httpx.Response(200, text="visitor page"),
            "https://passport.weibo.com/visitor/genvisitor": generation,
            (
                "https://passport.weibo.com/visitor/visitor?a=incarnate&t=tid&w=3&c=100"
                "&gc=&cb=cross_domain&from=weibo&_rand=0.5"
            ): callback,
        }
    )
    for _ in range(2):
        info = await weibo.WeiBo().parse_post("post", "https://weibo.com/123/post")
        assert info.images[0].url == "https://cdn.test/raw.jpg"
    assert counts == {"guest": 2, "status": 2}


def test_html_post_does_not_drop_its_video_as_if_it_were_a_photo_only_post():
    status = {
        "page_info": {
            "media_info": {
                "playback_list": [{"play_info": {"url": "https://cdn.test/video.mp4", "width": 720, "height": 1280}}]
            }
        }
    }
    html = "var $render_data = " + json.dumps([{"status": status}]) + "[0]"
    info = weibo.WeiBo._from_page(html)
    assert info.video_url == "https://cdn.test/video.mp4" and info.images == []
