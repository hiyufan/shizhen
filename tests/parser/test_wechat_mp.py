"""微信公众号文章：页面里 JS 字面量的读取、三种文章类型的图片 / 视频、各种拿不到的页面。

页面是照真实结构缩写的假页面，不碰网络。
"""

import asyncio

import httpx
import pytest

from qiuyuantu.parser import VideoSource, detect_source, wechat_mp
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.js_literal import parse_at
from qiuyuantu.parser.wechat_mp import WeChatMP

IMG = "https://mmbiz.qpic.cn/sz_mmbiz_jpg/AbC/0?wx_fmt=jpeg"
TRACKS = """mp_video_trans_info: [
    {format_id: '10002' * 1, width: '1920' * 1, height: '1080' * 1, filesize: '20012056',
     url: 'http://mpvideo.qpic.cn/hd.f10002.mp4?dis_k=a\\x26amp;dis_t=1',},
    {format_id: '10004' * 1, width: '854' * 1, height: '480' * 1, filesize: '4586258',
     url: 'http://mpvideo.qpic.cn/sd.f10004.mp4?dis_k=b\\x26amp;dis_t=1',},
  ],"""


def page(kind, body):
    return f"""<html><script nonce="1" reportloaderror>
  (function() {{
    try {{
      window.cgiDataNew = {{
        base_resp: {{ ret: '0' * 1, }},
        user_name: 'gh_123',
        nick_name: '某公众号',
        round_head_img: 'http://mmbiz.qpic.cn/mmbiz_png/head/0?wx_fmt=png',
        title: '标题 \\x26amp; 副标题',
        link: 'https://mp.weixin.qq.com/s/AbCdEf',
        cdn_url: 'http://mmbiz.qpic.cn/mmbiz_jpg/cover/0?wx_fmt=jpeg',
        item_show_type: '{kind}' * 1,
        link_type: 'LINK_TYPE_MP_APPMSG' * 1,
        {body}
      }};
    }} catch (e) {{}}
  }})();
</script></html>"""


PICTURE = page(
    8,
    """picture_page_info_list: [
      {cdn_url: 'https://mmbiz.qpic.cn/mmbiz_jpg/p1/0?wx_fmt=jpeg', width: '1152' * 1,
       watermark_info: {cdn_url: 'http://mmbiz.qpic.cn/mmbiz_jpg/watermark/0?wx_fmt=jpeg',},},
      {cdn_url: 'http://mmbiz.qpic.cn/sz_mmbiz_jpg/p2/0?wx_fmt=jpeg', share_cover: {cdn_url: '',},},
    ],""",
)
VIDEO = page(5, f"video_page_info: {{video_id: 'wxv_1', cover_url: 'http://mmbiz.qpic.cn/mmbiz_jpg/vc/0', {TRACKS} }},")
CONTENT = (
    "\\x3cp\\x3e正文\\x3c/p\\x3e"
    "\\x3cimg class=\\x22rich_pages\\x22 data-src=\\x22https://mmbiz.qpic.cn/sz_mmbiz_jpg/AbC/640?wx_fmt=jpeg"
    "\\x26amp;from=appmsg\\x22 /\\x3e"
    "\\x3cimg data-src=\\x22https://mmbiz.qpic.cn/mmbiz_gif/Gif/640?wx_fmt=gif\\x22 /\\x3e"
    "\\x3ciframe class=\\x22video_iframe\\x22 data-mpvid=\\x22wxv_2\\x22\\x3e\\x3c/iframe\\x3e"
)
ARTICLE = page(
    0,
    f"""content_noencode: '{CONTENT}',
    video_page_infos: [
      {{video_id: 'wxv_9', {TRACKS} }},
      {{video_id: 'wxv_2', cover_url: 'http://mmbiz.qpic.cn/mmbiz_jpg/c2/0', {TRACKS} }},
    ],""",
)


def test_js_literal_reads_page_style_values():
    text = "x = {a: '12' * 1, 'b': \"\\u4e2d\\x26\", c: [1, 2.5, true, null,], d: 'NaN' * 1, e: {},};"
    assert parse_at(text, 4) == {"a": 12, "b": "中&", "c": [1, 2.5, True, None], "d": None, "e": {}}


@pytest.mark.parametrize("text", ["{a: foo()}", "{a: window.x}", "{a 1}", "[" * 100 + "]" * 100])
def test_js_literal_refuses_code(text):
    with pytest.raises(ValueError):
        parse_at(text, 0)


@pytest.mark.parametrize(
    "url",
    [
        "https://mp.weixin.qq.com/s/AbCdEf",
        "https://mp.weixin.qq.com/s?__biz=MzA==&mid=1&idx=1&sn=abc&chksm=x#rd",
    ],
)
def test_article_links_are_routed(url):
    assert detect_source(url) == VideoSource.WeChatMP


@pytest.mark.parametrize(
    ("url", "original"),
    [
        ("https://mmbiz.qpic.cn/sz_mmbiz_jpg/AbC/640?wx_fmt=jpeg&amp;from=appmsg&amp;wxfrom=13", IMG),
        ("http://mmbiz.qpic.cn/sz_mmbiz_jpg/AbC/640", "https://mmbiz.qpic.cn/sz_mmbiz_jpg/AbC/0"),
        ("https://example.com/a/640?x=1", "https://example.com/a/640?x=1"),
    ],
)
def test_original_image(url, original):
    assert wechat_mp.original_image(url) == original


@pytest.mark.parametrize(
    ("url", "standard"),
    [
        (
            "https://mp.weixin.qq.com/s?__biz=MzA%3D%3D&mid=22&idx=1&sn=abc&source=41&poc_token=HMx",
            "https://mp.weixin.qq.com/s?__biz=MzA%3D%3D&mid=22&idx=1&sn=abc&chksm=1",
        ),
        (
            "https://mp.weixin.qq.com/s?search_click_id=9&__biz=MzA==&mid=22&idx=2&sn=abc&chksm=e5f&scene=21#rd",
            "https://mp.weixin.qq.com/s?__biz=MzA%3D%3D&mid=22&idx=2&sn=abc&chksm=e5f",
        ),
        ("https://mp.weixin.qq.com/s/AbCdEf", "https://mp.weixin.qq.com/s/AbCdEf"),
        ("https://mp.weixin.qq.com/s?__biz=MzA==&mid=22", "https://mp.weixin.qq.com/s?__biz=MzA==&mid=22"),
    ],
)
def test_old_style_links_are_requested_in_standard_form(url, standard):
    assert wechat_mp.standard_url(url) == standard


def test_picture_message_uses_original_pictures():
    info = wechat_mp.to_video_info(wechat_mp.page_data(PICTURE), "https://mp.weixin.qq.com/s/AbCdEf")
    # 水印图、分享封面不是正文图片
    assert [i.url for i in info.images] == [
        "https://mmbiz.qpic.cn/mmbiz_jpg/p1/0?wx_fmt=jpeg",
        "https://mmbiz.qpic.cn/sz_mmbiz_jpg/p2/0?wx_fmt=jpeg",
    ]
    assert (info.title, info.source, info.video_url) == ("标题 & 副标题", "wechat-mp", "")
    assert (info.author.uid, info.author.name) == ("gh_123", "某公众号")
    assert info.cover_url == "https://mmbiz.qpic.cn/mmbiz_jpg/cover/0?wx_fmt=jpeg"


def test_video_message_keeps_every_quality():
    info = wechat_mp.to_video_info(wechat_mp.page_data(VIDEO), "https://mp.weixin.qq.com/s/AbCdEf")
    assert info.video_url == "https://mpvideo.qpic.cn/hd.f10002.mp4?dis_k=a&dis_t=1"
    assert (info.width, info.height) == (1920, 1080)
    assert [(f.url, f.label, f.filesize) for f in info.formats] == [
        ("https://mpvideo.qpic.cn/sd.f10004.mp4?dis_k=b&dis_t=1", "480p", 4586258)
    ]
    assert info.cover_url == "https://mmbiz.qpic.cn/mmbiz_jpg/vc/0"


def test_article_images_and_embedded_video():
    info = wechat_mp.to_video_info(wechat_mp.page_data(ARTICLE), "https://mp.weixin.qq.com/s/AbCdEf")
    assert [i.url for i in info.images] == [IMG, "https://mmbiz.qpic.cn/mmbiz_gif/Gif/0?wx_fmt=gif"]
    # 只取正文里真的嵌了的视频（wxv_2），不是数据里列出的每一个
    assert info.video_url.startswith("https://mpvideo.qpic.cn/hd") and info.cover_url.endswith("/c2/0")
    assert info.notice == ""


def test_article_with_several_videos_lists_them_as_assets():
    data = wechat_mp.page_data(ARTICLE)
    data["content_noencode"] += '<iframe data-mpvid="wxv_9"></iframe>'
    info = wechat_mp.to_video_info(data, "https://mp.weixin.qq.com/s/AbCdEf")
    assert info.video_url == ""
    assert [(a.kind, a.url.split("?")[0]) for a in info.assets] == [
        ("video", "https://mpvideo.qpic.cn/hd.f10002.mp4"),
        ("video", "https://mpvideo.qpic.cn/hd.f10002.mp4"),
    ]


def test_article_with_only_channels_embed_says_why():
    data = wechat_mp.page_data(ARTICLE)
    data["content_noencode"] = '<section class="channels_iframe_wrp"></section>'
    with pytest.raises(ParseError) as exc:
        wechat_mp.to_video_info(data, "https://mp.weixin.qq.com/s/AbCdEf")
    assert exc.value.reason == "empty" and exc.value.detail == wechat_mp.EMBED_NOTICE


@pytest.mark.parametrize(
    ("html", "final_url", "reason", "detail"),
    [
        ('<div class="weui-msg__title warn">参数错误</div>', "", "deleted", "参数错误"),
        ('<h2 class="weui-msg__title">该内容已被发布者删除</h2>', "", "deleted", "该内容已被发布者删除"),
        ('<h2 class="weui-msg__title">此内容因违规无法查看</h2>', "", "restricted", "此内容因违规无法查看"),
        ("<title>未知错误</title><p>未知错误，请稍后再试</p>", "", "restricted", wechat_mp.CAPTCHA_HINT),
        (
            "<p>x</p>",
            "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=1",
            "restricted",
            wechat_mp.CAPTCHA_HINT,
        ),
        ("<html>改版了</html>", "", "parse", "页面里没有文章数据"),
    ],
)
def test_pages_without_article_data(html, final_url, reason, detail):
    error = wechat_mp.page_error(html, final_url)
    assert (error.reason, error.detail) == (reason, detail)


def test_challenge_is_retried_once_then_explained(monkeypatch):
    pages = iter(
        [
            httpx.Response(200, text="<p>未知错误</p>", request=httpx.Request("GET", "https://mp.weixin.qq.com/s/A")),
            httpx.Response(200, text=PICTURE, request=httpx.Request("GET", "https://mp.weixin.qq.com/s/A")),
        ]
    )

    async def fetch(self, method, url, **kw):
        assert kw["headers"]["User-Agent"].startswith("Mozilla/5.0 (Windows")
        return next(pages)

    monkeypatch.setattr(WeChatMP, "fetch", fetch)
    assert len(asyncio.run(WeChatMP().parse_share_url("https://mp.weixin.qq.com/s/A")).images) == 2

    blocked = httpx.Request("GET", "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=1")
    requested = []

    def challenged(self, method, url, **kw):
        requested.append(url)
        return _response(blocked)

    monkeypatch.setattr(WeChatMP, "fetch", challenged)
    with pytest.raises(ParseError) as exc:
        asyncio.run(WeChatMP().parse_share_url("https://mp.weixin.qq.com/s?__biz=MzA==&mid=1&idx=1&sn=abc&poc_token=x"))
    assert exc.value.detail == wechat_mp.CAPTCHA_HINT
    assert requested == ["https://mp.weixin.qq.com/s?__biz=MzA%3D%3D&mid=1&idx=1&sn=abc&chksm=1"] * 2


async def _response(request):
    return httpx.Response(200, text="<title>未知错误</title>", request=request)
