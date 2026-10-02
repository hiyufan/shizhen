"""上游搬来的小平台解析器：接口 / 页面返回 -> VideoInfo。HTTP 全换成假的，不碰网络。

每个用例给出「哪个地址返回什么」，断言解析结果的关键字段。数据结构照解析器实际读取的字段造，
平台改版时以真实返回为准改这里。
"""

import asyncio
import base64
import importlib
import json
import pkgutil

import httpx
import pytest

from parse_video_py import parser as parser_pkg
from parse_video_py.parser.acfun import AcFun
from parse_video_py.parser.cctv import CCTV
from parse_video_py.parser.doupai import DouPai
from parse_video_py.parser.errors import ParseError, classify
from parse_video_py.parser.haokan import HaoKan
from parse_video_py.parser.huya import HuYa
from parse_video_py.parser.lishipin import LiShiPin
from parse_video_py.parser.lvzhou import LvZhou
from parse_video_py.parser.pipigaoxiao import PiPiGaoXiao
from parse_video_py.parser.pipixia import PiPiXia
from parse_video_py.parser.qqvideo import QQVideo
from parse_video_py.parser.quanmin import QuanMin
from parse_video_py.parser.quanminkge import QuanMinKGe
from parse_video_py.parser.sixroom import SixRoom
from parse_video_py.parser.sohu import Sohu
from parse_video_py.parser.weibo import WeiBo
from parse_video_py.parser.weishi import WeiShi
from parse_video_py.parser.xinpianchang import XinPianChang
from parse_video_py.parser.zuiyou import ZuiYou


class FakeClient:
    """按地址前缀回放的假 httpx 客户端；没配的地址直接让测试失败。"""

    def __init__(self, routes, calls):
        self.routes, self.calls = routes, calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for prefix, reply in self.routes.items():
            if url.startswith(prefix):
                status, body, headers = reply if isinstance(reply, tuple) else (200, reply, {})
                content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                return httpx.Response(status, text=content, headers=headers, request=httpx.Request(method, url))
        raise AssertionError(f"没有配置的地址：{method} {url}")

    async def get(self, url, **kw):
        return await self.request("GET", url, **kw)

    async def post(self, url, **kw):
        return await self.request("POST", url, **kw)


@pytest.fixture
def http(monkeypatch):
    """http(routes) 之后解析器发的请求都由 routes 回答；返回记下来的请求。"""
    calls = []

    def install(routes):
        def factory(**_kw):
            return FakeClient(routes, calls)

        for info in pkgutil.iter_modules(parser_pkg.__path__):
            module = importlib.import_module(f"parse_video_py.parser.{info.name}")
            if hasattr(module, "create_async_client"):
                monkeypatch.setattr(module, "create_async_client", factory)
        return calls

    return install


def run(coro):
    return asyncio.run(coro)


def summary(info):
    return {
        "video": info.video_url,
        "cover": info.cover_url,
        "title": info.title,
        "author": (info.author.uid, info.author.name, info.author.avatar),
        "images": [(i.url, i.live_photo_url) for i in info.images],
    }


# --------------------------------------------------------------------------- 接口直接给 JSON 的


def test_doupai(http):
    http(
        {
            "https://v2.doupai.cc/topic/42.json": {
                "data": {
                    "videoUrl": "v.mp4",
                    "imageUrl": "c.jpg",
                    "name": "标题",
                    "userId": {"id": "u1", "name": "作者", "avatar": "a.jpg"},
                }
            }
        }
    )
    info = run(DouPai().parse_share_url("https://doupai.cc/x?id=42"))
    assert summary(info) == {
        "video": "v.mp4",
        "cover": "c.jpg",
        "title": "标题",
        "author": ("u1", "作者", "a.jpg"),
        "images": [],
    }


def test_haokan(http):
    meta = {
        "playurl": "v.mp4",
        "poster": "c.jpg",
        "title": "好看",
        "mth": {"mthid": "m1", "author_name": "作者", "author_photo": "a.jpg"},
    }
    http({"https://haokan.baidu.com/v?_format=json&vid=9": {"errno": 0, "data": {"apiData": {"curVideoMeta": meta}}}})
    info = run(HaoKan().parse_share_url("https://haokan.baidu.com/v?vid=9"))
    assert (info.video_url, info.title, info.author.uid) == ("v.mp4", "好看", "m1")


def test_haokan_error_message_is_kept_for_classification(http):
    http({"https://haokan.baidu.com/v?_format=json&vid=9": {"errno": 1, "error": "视频不存在"}})
    with pytest.raises(Exception, match="视频不存在") as exc:
        run(HaoKan().parse_share_url("https://haokan.baidu.com/v?vid=9"))
    assert classify(exc.value).reason == "deleted"


def test_huya(http):
    video = {
        "uid": 7,
        "definitions": [{"url": "v.mp4"}],
        "videoCover": "c.jpg",
        "videoTitle": "虎牙",
        "actorNick": "主播",
        "actorAvatarUrl": "a.jpg",
    }
    calls = http(
        {"https://liveapi.huya.com/moment/getMomentContent?videoId=123": {"data": {"moment": {"videoInfo": video}}}}
    )
    info = run(HuYa().parse_share_url("https://v.huya.com/play/123.html"))
    assert summary(info)["author"] == ("7", "主播", "a.jpg") and info.video_url == "v.mp4"
    assert calls[0][2]["headers"]["Referer"] == "https://v.huya.com/"


def test_huya_missing_video_is_reported_as_deleted(http):
    http({"https://liveapi.huya.com/": {"data": {"moment": {"videoInfo": {"uid": 0}}}}})
    with pytest.raises(Exception) as exc:
        run(HuYa().parse_share_url("https://v.huya.com/play/123.html"))
    assert classify(exc.value).reason == "deleted"


def test_lishipin(http):
    http(
        {
            "https://www.pearvideo.com/videoStatus.jsp?contId=1785": {
                "systemTime": "1700000000",
                "videoInfo": {
                    "video_image": "c.jpg",
                    "videos": {"srcUrl": "https://video.pearvideo.com/mp4/1700000000-15-hd.mp4"},
                },
            }
        }
    )
    info = run(LiShiPin().parse_share_url("https://www.pearvideo.com/detail_1785"))
    assert info.video_url == "https://video.pearvideo.com/mp4/cont-1785-15-hd.mp4" and info.cover_url == "c.jpg"


def test_pipigaoxiao(http):
    post = {"imgs": [{"id": 5}], "videos": {"5": {"url": "v.mp4"}}, "content": "搞笑"}
    calls = http({"https://share.ippzone.com/ppapi/share/fetch_content": {"data": {"post": post}}})
    info = run(PiPiGaoXiao().parse_share_url("https://h5.pipigx.com/pp/post/777"))
    assert (info.video_url, info.cover_url, info.title) == ("v.mp4", "https://file.ippzone.com/img/view/id/5", "搞笑")
    assert calls[0][2]["content"] == '{"pid":777,"type":"post","mid":null}'


def test_pipixia_prefers_unwatermarked_video_from_author_comment(http):
    def video(url):
        return {"video_high": {"url_list": [{"url": url}]}}

    item = {
        "author": {"id": 9, "name": "作者", "avatar": {"download_list": [{"url": "a.jpg"}]}},
        "cover": {"url_list": [{"url": "c.jpg"}]},
        "content": "皮皮虾",
        "video": video("watermark.mp4"),
        "comments": [
            {"item": {"author": {"id": 1}, "video": video("other.mp4")}},
            {"item": {"author": {"id": 9}, "video": video("clean.mp4")}},
        ],
    }
    http(
        {
            "https://h5.pipix.com/s/abc": (302, "", {"location": "https://h5.pipix.com/item/6901?app=1"}),
            "https://api.pipix.com/bds/cell/cell_comment/?offset=0&cell_type=1&api_version=1&cell_id=6901": {
                "status_code": 0,
                "data": {"cell_comments": [{"comment_info": {"item": item}}]},
            },
        }
    )
    info = run(PiPiXia().parse_share_url("https://h5.pipix.com/s/abc"))
    assert summary(info) == {
        "video": "clean.mp4",
        "cover": "c.jpg",
        "title": "皮皮虾",
        "author": ("9", "作者", "a.jpg"),
        "images": [],
    }


def test_pipixia_image_note(http):
    item = {
        "author": {"id": 9, "name": "n", "avatar": {"download_list": [{"url": "a"}]}},
        "cover": {"url_list": [{"url": "c"}]},
        "content": "图",
        "note": {"multi_image": [{"url_list": [{"url": "1.jpg"}]}, {"url_list": [{"url": "2.jpg"}]}]},
    }
    http({"https://api.pipix.com/": {"status_code": 0, "data": {"cell_comments": [{"comment_info": {"item": item}}]}}})
    info = run(PiPiXia().parse_video_id("1"))
    assert info.video_url == "" and [i.url for i in info.images] == ["1.jpg", "2.jpg"]


def test_quanmin(http):
    data = {
        "meta": {
            "statusText": "",
            "title": "",
            "image": "c.jpg",
            "video_info": {"clarityUrl": [{"url": "low.mp4"}, {"url": "hd.mp4"}]},
        },
        "shareInfo": {"title": "分享标题"},
        "author": {"id": "a1", "name": "作者", "icon": "a.jpg"},
    }
    http({"https://quanmin.hao222.com/wise/growth/api/sv/immerse": {"errno": 0, "data": data}})
    info = run(QuanMin().parse_share_url("https://xspshare.baidu.com/x?vid=3"))
    assert (info.video_url, info.title, info.author.uid) == ("hd.mp4", "分享标题", "a1")


def test_quanminkge(http):
    detail = {"playurl_video": "v.mp4", "cover": "c.jpg", "content": "唱歌", "uid": "u", "nick": "歌手", "avatar": "a"}
    http(
        {"https://kg.qq.com/node/play?s=SID": f"<script>window.__DATA__ = {json.dumps({'detail': detail})}; </script>"}
    )
    info = run(QuanMinKGe().parse_share_url("https://kg.qq.com/node/play?s=SID"))
    assert (info.video_url, info.title, info.author.name) == ("v.mp4", "唱歌", "歌手")


def test_sixroom(http):
    content = {"playurl": "v.mp4", "picurl": "c.jpg", "title": "六间房", "alias": "主播", "picuser": "a.jpg"}
    calls = http({"https://v.6.cn/coop/mobile/index.php": {"content": content}})
    info = run(SixRoom().parse_share_url("https://m.6.cn/v/ABC/"))
    assert summary(info)["author"] == ("", "主播", "a.jpg")
    assert calls[0][1].endswith("&vid=ABC")


def test_weishi(http):
    feed = {
        "video_url": "v.mp4",
        "images": [{"url": "c.jpg"}],
        "feed_desc_withat": "微视",
        "id": "f1",
        "poster": {"nick": "作者", "avatar": "a.jpg"},
    }
    http({"https://h5.weishi.qq.com/": {"ret": 0, "data": {"errmsg": "", "feeds": [feed]}}})
    info = run(WeiShi().parse_share_url("https://isee.weishi.qq.com/x?id=f1"))
    assert (info.video_url, info.cover_url, info.title) == ("v.mp4", "c.jpg", "微视")


def test_zuiyou(http):
    post = {
        "imgs": [{"id": 3}],
        "videos": {"3": {"url": "v.mp4"}},
        "content": "最右",
        "member": {"id": 8, "name": "作者", "avatar_urls": {"origin": {"urls": ["a.jpg"]}}},
    }
    calls = http({"https://share.xiaochuankeji.cn/planck/share/post/detail_h5": {"data": {"post": post}}})
    info = run(ZuiYou().parse_share_url("https://share.xiaochuankeji.cn/x?pid=55"))
    assert summary(info)["author"] == ("8", "作者", "a.jpg") and calls[0][2]["json"]["pid"] == 55


def test_cctv(http):
    http(
        {
            "https://tv.cctv.com/2024/v.shtml": '<script>var guid = "GUID123";</script>',
            "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid=GUID123": {
                "status": "001",
                "hls_url": "v.m3u8",
                "title": "新闻",
                "image": "c.jpg",
                "play_channel": "CCTV-1",
            },
        }
    )
    info = run(CCTV().parse_share_url("https://tv.cctv.com/2024/v.shtml"))
    assert (info.video_url, info.title, info.author.name) == ("v.m3u8", "新闻", "CCTV-1")


def test_qqvideo(http):
    vi = {"ul": {"ui": [{"url": "https://cdn/"}]}, "fn": "a.mp4", "fvkey": "K", "vid": "v0", "ti": "腾讯"}
    http({"https://vv.video.qq.com/getinfo?vids=v0": "QZOutputJson=" + json.dumps({"vl": {"vi": [vi]}}) + ";"})
    info = run(QQVideo().parse_share_url("https://v.qq.com/x/page/v0.html"))
    assert info.video_url == "https://cdn/a.mp4?vkey=K" and info.title == "腾讯"
    assert info.cover_url == "https://puui.qpic.cn/vpic_cover/v0/v0_hz.jpg/496"


def test_qqvideo_deleted(http):
    http({"https://vv.video.qq.com/": "QZOutputJson=" + json.dumps({"vl": {"vi": []}}) + ";"})
    with pytest.raises(Exception) as exc:
        run(QQVideo().parse_video_id("v0"))
    assert classify(exc.value).reason == "deleted"


def test_sohu(http):
    data = {
        "url_high_mp4": "",
        "download_url": "d.mp4",
        "video_name": "搜狐",
        "originalCutCover": "c.jpg",
        "user": {"user_id": 5, "nickname": "作者", "small_pic": "a.jpg"},
    }
    http({"https://api.tv.sohu.com/v4/video/info/123.json": {"status": 200, "data": data}})
    url = "https://tv.sohu.com/v/" + base64.b64encode(b"us/1/123.shtml").decode() + ".html"
    info = run(Sohu().parse_share_url(url))
    assert summary(info) == {
        "video": "d.mp4",
        "cover": "c.jpg",
        "title": "搜狐",
        "author": ("5", "作者", "a.jpg"),
        "images": [],
    }


# --------------------------------------------------------------------------- 从页面里取的


def test_acfun(http):
    html = (
        '<script>var videoInfo = {"cover": "c.jpg", "title": "A站"};\n'
        'var playInfo = {"streams": [{"playUrls": ["v.m3u8"]}]};</script>'
        '<div class="up-info"><a class="info-item1" href="/upPage/42">x</a><span class="up-name">UP</span>'
        '<span class="up-avatar"><img src="a.jpg"></span></div>'
    )
    http({"https://www.acfun.cn/v/ac1": html})
    info = run(AcFun().parse_video_id("ac1"))
    assert summary(info) == {
        "video": "v.m3u8",
        "cover": "c.jpg",
        "title": "A站",
        "author": ("42", "UP", "a.jpg"),
        "images": [],
    }


def test_lvzhou(http):
    html = (
        '<video src="v.mp4"></video><a class="avatar"><img src="a.jpg"></a>'
        '<div class="video-cover" style="background-image:url(c.jpg)"></div>'
        '<div class="status-title">绿洲</div><div class="nickname">作者</div>'
    )
    http({"https://m.oasis.weibo.cn/v1/h5/share?sid=7": html})
    info = run(LvZhou().parse_video_id("7"))
    assert summary(info) == {
        "video": "v.mp4",
        "cover": "c.jpg",
        "title": "绿洲",
        "author": ("", "作者", "a.jpg"),
        "images": [],
    }


def test_xinpianchang(http):
    detail = {
        "video": {"appKey": "K"},
        "media_id": "M",
        "cover": "c.jpg",
        "title": "片子",
        "author": {"userinfo": {"id": 3, "username": "导演", "avatar": "a.jpg"}},
    }
    page = {"props": {"pageProps": {"detail": detail}}}
    http(
        {
            "https://www.xinpianchang.com/a1": f'<script id="__NEXT_DATA__">{json.dumps(page)}</script>',
            "https://mod-api.xinpianchang.com/mod/api/v2/media/M?appKey=K": {
                "data": {"resource": {"progressive": [{"url": "v.mp4"}]}}
            },
        }
    )
    info = run(XinPianChang().parse_share_url("https://www.xinpianchang.com/a1"))
    assert summary(info) == {
        "video": "v.mp4",
        "cover": "c.jpg",
        "title": "片子",
        "author": ("3", "导演", "a.jpg"),
        "images": [],
    }


# --------------------------------------------------------------------------- 微博


WEIBO_STATUS = {
    "text": "<a>#话题#</a> 图片微博 ",
    "user": {"screen_name": "博主", "avatar_large": "a.jpg"},
    "pics": [{"large": {"url": "1-large.jpg"}, "url": "1.jpg"}, {"bmiddle": {"url": "2-mid.jpg"}}, {"other": {}}],
}


def test_weibo_video(http):
    play = {
        "stream_url": "low.mp4",
        "urls": {"高清 1080P": "//f.video/hd.mp4"},
        "cover_image": "//c.jpg",
        "title": "微博视频",
        "user": {"id": 1},
        "author": "博主",
        "avatar": "//a.jpg",
    }
    calls = http(
        {"https://h5.video.weibo.com/api/component?page=/show/1034:99": {"data": {"Component_Play_Playinfo": play}}}
    )
    info = run(WeiBo().parse_share_url("https://weibo.com/tv/show/1034:99"))
    assert summary(info) == {
        "video": "https://f.video/hd.mp4",
        "cover": "https://c.jpg",
        "title": "微博视频",
        "author": ("1", "博主", "https://a.jpg"),
        "images": [],
    }
    assert calls[0][2]["content"] == 'data={"Component_Play_Playinfo":{"oid":"1034:99"}}'


def test_weibo_image_post_from_mobile_api(http):
    http({"https://m.weibo.cn/statuses/show?id=Q9pc": {"data": WEIBO_STATUS}})
    info = run(WeiBo().parse_share_url("https://weibo.com/2543858012/Q9pc"))
    assert info.title == "#话题# 图片微博" and [i.url for i in info.images] == ["1-large.jpg", "2-mid.jpg"]
    assert summary(info)["author"] == ("", "博主", "a.jpg")


def test_weibo_image_post_falls_back_to_html(http):
    html = "<script>var $render_data = " + json.dumps([{"status": WEIBO_STATUS}]) + "[0] || {};</script>"
    http({"https://m.weibo.cn/statuses/show": (403, "", {}), "https://weibo.com/2543858012/Q9pc": html})
    info = run(WeiBo().parse_share_url("https://weibo.com/2543858012/Q9pc"))
    assert [i.url for i in info.images] == ["1-large.jpg", "2-mid.jpg"]


def test_unsupported_urls_say_so(http):
    http({})  # 一个请求都不该发
    for parser, url in [(WeiBo(), "https://weibo.com/"), (SixRoom(), "https://6.cn/")]:
        with pytest.raises(Exception) as exc:
            run(parser.parse_share_url(url))
        err = exc.value if isinstance(exc.value, ParseError) else classify(exc.value)
        assert err.reason == "unsupported", (url, exc.value)
