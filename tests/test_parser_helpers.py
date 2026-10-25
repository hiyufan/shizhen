"""解析器的离线行为：纯函数、链接识别与请求上下文。"""

import asyncio
import json

import httpx
import pytest

from qiuyuantu import parser as parser_pkg
from qiuyuantu import utils
from qiuyuantu.feedback import FIXABLE
from qiuyuantu.parser import VideoSource, detect_source
from qiuyuantu.parser import bilibili as bilibili_mod
from qiuyuantu.parser.bilibili import BiliBili, dash_formats, guest_params, id_param, opus_images, opus_text
from qiuyuantu.parser.douyin import _looks_like_note, _note_images
from qiuyuantu.parser.errors import ParseError


@pytest.mark.parametrize("error", [None, ValueError, asyncio.CancelledError])
async def test_id_parser_uses_platform_proxy_and_restores_context(monkeypatch, error):
    monkeypatch.setenv("PARSE_VIDEO_PROXY", "http://general:8080")
    monkeypatch.setenv("PARSE_VIDEO_PROXY_CN", "http://cn:8080")
    routes = []
    result = object()

    async def parse_id(self, video_id):
        assert video_id == "BV1GJ411x7h7"
        routes.append(utils.proxy_for())
        if error:
            raise error()
        return result

    monkeypatch.setattr(BiliBili, "parse_video_id", parse_id)
    previous = utils.current_source.set("youtube")
    try:
        if error:
            with pytest.raises(error):
                await parser_pkg.parse_video_id(VideoSource.BiliBili, "BV1GJ411x7h7")
        else:
            assert await parser_pkg.parse_video_id(VideoSource.BiliBili, "BV1GJ411x7h7") is result
        assert routes == ["http://cn:8080"]
        assert utils.current_source.get() == "youtube"
    finally:
        utils.current_source.reset(previous)


def test_av_ids_use_numeric_params():
    assert id_param("BV1GJ411x7h7", "aid") == "bvid=BV1GJ411x7h7"
    assert id_param("av170001", "aid") == "aid=170001"
    assert id_param("AV2", "avid") == "avid=2"
    # 不是纯数字的别误判
    assert id_param("avatar", "aid") == "bvid=avatar"


def test_dash_formats_only_above_progressive_height():
    play = {
        "dash": {
            "audio": [{"bandwidth": 64_000}, {"bandwidth": 192_000}],
            "video": [
                {"width": 1920, "height": 1080, "bandwidth": 2_000_000},
                {"width": 1920, "height": 1080, "bandwidth": 3_000_000},
                {"width": 1280, "height": 720, "bandwidth": 1_000_000},
            ],
        }
    }
    fmts = dash_formats(play, duration=100, above=720)
    assert [f.label for f in fmts] == ["1080p", "仅音频"]
    # 同一高度取码率最高的那条，体积 = (视频 + 最好的音频) × 时长
    assert fmts[0].filesize == (3_000_000 + 192_000) * 100 // 8
    assert "height<=1080" in fmts[0].format_spec


def test_vertical_video_labels_by_short_side_but_filters_by_height():
    play = {"dash": {"audio": [], "video": [{"width": 1080, "height": 1920, "bandwidth": 1}]}}
    (fmt,) = dash_formats(play, duration=10, above=720)
    assert fmt.label == "1080p"
    assert "height<=1920" in fmt.format_spec


def test_guest_params_shape():
    p = guest_params()
    assert p["try_look"] == 1
    inter = json.loads(p["dm_img_inter"])
    assert set(inter) == {"ds", "wh", "of"}
    assert " " not in p["dm_img_inter"], "B 站要紧凑 JSON"


def test_detect_source_matches_host_not_substring():
    # 以前在整个链接里找子串：pinterest.com / reddit.com 里有 "t.co"，pipix.com 里有 "x.com"
    assert detect_source("https://www.pinterest.com/pin/123") == VideoSource.YtDlp
    assert detect_source("https://www.reddit.com/r/a/comments/1") == VideoSource.YtDlp
    assert detect_source("https://www.youtube.com/watch?v=1&ref=t.co") == VideoSource.YtDlp
    assert detect_source("https://h5.pipix.com/item/1") == VideoSource.PiPiXia
    assert detect_source("https://example.com/?u=https://v.douyin.com/x/") == VideoSource.YtDlp
    # 本身和子域名都认，大小写不敏感
    assert detect_source("https://t.co/abc") == VideoSource.Twitter
    assert detect_source("https://X.com/a/status/1") == VideoSource.Twitter
    assert detect_source("https://mobile.twitter.com/a/status/1") == VideoSource.Twitter
    # 微博手机链接是微博的，以前绿洲登记了整个 weibo.cn 把它抢走了（2026-10-14 实测失败）
    assert detect_source("https://m.weibo.cn/status/1") == VideoSource.WeiBo
    assert detect_source("https://m.oasis.weibo.cn/v1/h5/share?sid=7") == VideoSource.LvZhou
    assert detect_source("https://v.douyin.com/irXxc3GY/") == VideoSource.DouYin
    assert detect_source("not a url") == VideoSource.YtDlp


def test_douyin_note_urls():
    assert _looks_like_note("https://www.douyin.com/note/7424432820954598707")
    assert _looks_like_note("https://www.iesdouyin.com/share/note/7424432820954598707/?from=web")
    assert _looks_like_note("https://www.iesdouyin.com/share/slides/7424432820954598707/")
    assert not _looks_like_note("https://www.iesdouyin.com/share/video/7424432820954598707/")


def test_douyin_browser_note_lives_from_aweme_info():
    a = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oAD7QVQrR~tplv-dy-aweme-images:q75.jpeg?x-expires=1"
    b = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/ocG9IGDnA~tplv-dy-aweme-images:q75.jpeg?x-expires=1"
    c = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/ooRQAr9Df~tplv-dy-aweme-images:q75.jpeg?x-expires=1"
    lives = {"ocG9IGDnA": "https://v/b.mp4", "oAD7QVQrR": "https://v/a.mp4"}  # c 没有实况
    imgs = _note_images({"images": [a, b, c], "lives": lives, "live": "https://v/dom.mp4"})
    assert [i.live_photo_url for i in imgs] == ["https://v/a.mp4", "https://v/b.mp4", ""]
    # awemeInfo 说这张没实况就是没有，不拿 DOM 里的视频去凑
    (img,) = _note_images({"images": [c], "lives": {}, "live": "https://v/dom.mp4"})
    assert img.live_photo_url == ""


def test_douyin_browser_note_dom_fallback_only_for_single_image():
    live = "https://v5-hl-zenl-ov.zjcdn.com/x/6abdc803/video/tos/cn/tos-cn-ve-15/abc/"
    (img,) = _note_images({"images": ["https://p3-pc-sign.douyinpic.com/a"], "lives": None, "live": live})
    assert img.live_photo_url == live
    # 多张时 DOM 里只有前后几页的视频，配不准就不配
    imgs = _note_images({"images": ["https://p3/a", "https://p3/b"], "lives": None, "live": live})
    assert [i.live_photo_url for i in imgs] == ["", ""]
    (img,) = _note_images({"images": ["https://p3/a"], "live": ""})
    assert img.live_photo_url == ""


def test_douyin_browser_note_prefers_jpeg_from_aweme_info():
    # 网页显示 q75 webp；awemeInfo 里同一张图有原尺寸 JPEG，按 id 换过去，没有的保留 webp
    a = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oAD7QVQrR~tplv-dy-aweme-images:q75.webp?x-expires=1"
    b = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/ocG9IGDnA~tplv-dy-aweme-images:q75.webp?x-expires=1"
    jpeg_a = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oAD7QVQrR~tplv-dy-aweme-images:q75.jpeg?x-expires=2"
    imgs = _note_images({"images": [a, b], "lives": {"oAD7QVQrR": "https://v/a.mp4"}, "jpegs": {"oAD7QVQrR": jpeg_a}})
    assert [i.url for i in imgs] == [jpeg_a, b]
    assert imgs[0].live_photo_url == "https://v/a.mp4"  # 换了地址实况照样配得上


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/bangumi/play/ep690878?spm_id_from=333.337.0.0",
        "https://m.bilibili.com/bangumi/play/ss41417",
        "https://www.bilibili.com/cheese/play/ep123",
    ],
)
async def test_bili_copyright_content_fails_fast(url, monkeypatch):
    """番剧 / 电影 / 课程不支持（issue #4）：不请求接口、不退回 yt-dlp，也不让用户点反馈。"""

    async def no_ytdlp(_url):
        raise AssertionError("不该退回 yt-dlp")

    monkeypatch.setattr(parser_pkg, "_ytdlp_extra", no_ytdlp)
    with pytest.raises(ParseError) as exc:
        await parser_pkg.parse_video_share_url(url)
    assert exc.value.reason == "copyright" and exc.value.reason not in FIXABLE


async def test_bili_opus_images_and_links():
    """B站动态：图集和正文里的图都要，实况带 live_url；三种链接写法都认（2026-10-13 有人试了 4 次）。"""
    modules = {
        "MODULE_TYPE_TOP": {
            "module_top": {
                "display": {
                    "album": {
                        "pics": [
                            {"url": "http://i0.hdslb.com/bfs/new_dyn/a.jpg", "live_url": "https://i0.hdslb.com/v.mp4"}
                        ]
                    }
                }
            }
        },
        "MODULE_TYPE_CONTENT": {
            "module_content": {
                "paragraphs": [
                    {"text": {"nodes": [{"word": {"words": "想我了"}}, {"word": {"words": "没"}}]}},
                    {"pic": {"pics": [{"url": "https://i0.hdslb.com/bfs/b.jpg"}]}},
                ]
            }
        },
    }
    assert [(i.url, i.live_photo_url) for i in opus_images(modules)] == [
        ("https://i0.hdslb.com/bfs/new_dyn/a.jpg", "https://i0.hdslb.com/v.mp4"),
        ("https://i0.hdslb.com/bfs/b.jpg", ""),
    ]
    assert opus_text(modules) == "想我了没"
    for url in (
        "https://m.bilibili.com/opus/1257456486020284425?plat_id=186",
        "https://www.bilibili.com/opus/1257456486020284425",
        "https://t.bilibili.com/1257456486020284425",
    ):
        assert await BiliBili()._get_bvid_from_url(url) == "opus1257456486020284425"
    assert detect_source("https://t.bilibili.com/1257456486020284425") == VideoSource.BiliBili


@pytest.mark.parametrize(
    ("url", "kind", "source"),
    [
        (
            "https://p11-sign.douyinpic.com/tos-cn-i-0813c000-ce/oo9r~tplv-dy-lqen-new:1192:670:q80.webp?x-expires=1",
            "image",
            "douyin",
        ),
        ("https://sns-webpic-qc.xhscdn.com/202610141200/abc/1040g2sg31!nd_dft_wlteh_webp_3", "image", "redbook"),
        ("https://video.twimg.com/ext_tw_video/1/pu/vid/avc1/720x1280/a.mp4?tag=12", "video", "twitter"),
        ("https://v26-weba.douyinvod.com/abc/6acf/video/tos/cn/tos-cn-ve-15/o42e/?a=6383", "video", "douyin"),
        ("https://v.douyin.com/u3knSHnayKo/", None, None),  # 分享链接照常解析
        ("https://x.com/a/status/1", None, None),
        ("https://example.com/a.jpg", None, None),  # 不认识的站点不管，交给 yt-dlp
    ],
)
def test_direct_media_links(url, kind, source):
    """平台 CDN 上的文件地址直接给出去（2026-10-10 有人贴抖音图片地址试了 3 次，以前交给 yt-dlp 必失败）。"""
    from qiuyuantu.parser.direct import direct_media

    info = direct_media(url)
    if kind is None:
        assert info is None
        return
    assert info.source == source and info.notice
    assert (info.video_url == url) if kind == "video" else ([i.url for i in info.images] == [url])


@pytest.mark.parametrize(
    ("text", "url"),
    [
        (
            "8.28 VLW:/ E@U.LJ :2pm 08/30 # 06 # 不纯男高  https://v.douyin.com/u3knSHnayKo/ 复制此链接，打开抖音",
            "https://v.douyin.com/u3knSHnayKo/",
        ),
        ("【小红书】这家店绝了😭 http://xhslink.com/m/1gJ5tOG6M1b 复制本条信息", "http://xhslink.com/m/1gJ5tOG6M1b"),
        ("【标题】https://b23.tv/xgrWG6H哔哩哔哩", "https://b23.tv/xgrWG6H"),  # 链接后面直接接中文
        ("《少女》@汽水音乐https://qishui.douyin.com/s/iXQkjGeP/", "https://qishui.douyin.com/s/iXQkjGeP/"),
        ("Look: https://x.com/a/status/1?s=20.", "https://x.com/a/status/1?s=20"),  # 句末英文句号不算
        ("https://x.com/a/status/1😂", "https://x.com/a/status/1"),
        # 以前在 @ ~ : = 处截断（2026-10-14）
        ("https://www.tiktok.com/@scout2015/video/6718335390845095173", None),
        (
            "https://p11-sign.douyinpic.com/tos-cn-i-0813c000-ce/oo9r~tplv-dy-lqen-new:1192:670:q80.webp?x-signature=r0%2B%3D",
            None,
        ),
        ("https://tv.sohu.com/v/MjAyNjEwMTMvbjYyMDI1NTEwOS5zaHRtbA==.html", None),
        ("没有链接", "NONE"),
    ],
)
def test_extract_url_from_share_text(text, url):
    from qiuyuantu.utils import extract_url

    if url == "NONE":
        assert extract_url(text) is None
    else:
        assert extract_url(text) == (url or text)


@pytest.mark.parametrize(
    ("url", "cid", "title", "page_url"),
    [
        (
            "https://www.bilibili.com/video/BV1x/?spm_id_from=333.788.videopod.episodes&p=13",
            13,
            "课 P13 第13讲",
            "https://www.bilibili.com/video/BV1x?p=13",
        ),
        ("https://www.bilibili.com/video/BV1x/", 1, "课 P1 第1讲", "https://www.bilibili.com/video/BV1x"),
        ("https://www.bilibili.com/video/BV1x?p=2", 2, "课 P2 第2讲", "https://www.bilibili.com/video/BV1x?p=2"),
        # 短链跳转到带 p 的长链接，分 P 跟着走
        ("https://b23.tv/abc", 13, "课 P13 第13讲", "https://www.bilibili.com/video/BV1x?p=13"),
        ("https://www.bilibili.com/video/BV1x?p=99", None, "", ""),  # 没有这一 P：明确报错，不悄悄换成 P1
    ],
)
async def test_bili_multi_part_uses_the_linked_part(monkeypatch, url, cid, title, page_url):
    """多 P 视频按链接里的 ?p= 取那一 P（issue #6：贴第 13P 给的是第一 P，时长还是 30 小时的总和）。"""
    pages = [{"page": i, "cid": i, "part": f"第{i}讲", "duration": i * 60} for i in range(1, 21)]
    played = []

    async def api(self, url):
        if "pagelist" in url:
            return {"code": 0, "data": pages}
        return {"code": 0, "data": {"bvid": "BV1x", "title": "课", "duration": 99999, "pages": pages}}

    async def play(self, video_id, c):
        played.append(c)
        return {"data": {}}, {"data": {"durl": [{"url": "https://upos.bilivideo.com/v.mp4"}], "quality": 64}}

    async def noop(self):
        return None

    class ShortLink:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers):
            return httpx.Response(302, headers={"location": "https://www.bilibili.com/video/BV1x/?p=13&share=1"})

    monkeypatch.setattr(BiliBili, "_api", api)
    monkeypatch.setattr(BiliBili, "_play_urls", play)
    monkeypatch.setattr(BiliBili, "_ensure_buvid", noop)
    monkeypatch.setattr(bilibili_mod, "create_async_client", lambda **kw: ShortLink())
    if cid is None:
        with pytest.raises(ParseError) as exc:
            await BiliBili().parse_share_url(url)
        assert exc.value.reason == "deleted" and "只有 20 P" in str(exc.value) and played == []
        return
    info = await BiliBili().parse_share_url(url)
    assert played == [cid] and (info.title, info.page_url, info.duration) == (title, page_url, cid * 60)
