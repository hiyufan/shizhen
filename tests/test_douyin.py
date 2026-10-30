"""抖音解析：作品数据、接口重试与浏览器兜底。接口换成假的，不碰网络。"""

import asyncio
import copy
import json

import httpx
import pytest

from qiuyuantu.parser import douyin
from qiuyuantu.parser.base import FormatInfo, ImgInfo, VideoInfo
from qiuyuantu.parser.douyin import DouYin
from qiuyuantu.parser.errors import ParseError

VIDEO_ID = "7424432820954598707"
PC_URL = f"https://www.douyin.com/video/{VIDEO_ID}"

VIDEO_AWEME = {
    "desc": "一条视频",
    "author": {
        "sec_uid": "MS4w",
        "nickname": "作者",
        "avatar_thumb": {"url_list": ["https://p3.douyinpic.com/a.jpeg"]},
    },
    "music": {"play_url": {"url_list": ["https://sf3.douyinstatic.com/m.mp3"], "uri": "m-uri"}},
    "video": {
        "duration": 15200,
        "play_addr_h264": {
            "url_list": ["https://v3-web.douyinvod.com/playwm/720.mp4"],
            "width": 720,
            "height": 1280,
            "uri": "v-uri",
            "url_key": "k720",
        },
        "cover": {"url_list": ["https://p3.douyinpic.com/c.webp?x=1", "https://p3.douyinpic.com/c.jpeg?x=1"]},
        "bit_rate": [
            {"play_addr": {"url_key": "k720", "url_list": ["https://v/720"], "width": 720, "height": 1280}},
            {
                "play_addr": {
                    "url_key": "k1080",
                    "url_list": ["https://v3-web.douyinvod.com/playwm/1080.mp4"],
                    "width": 1080,
                    "height": 1920,
                    "data_size": 9000,
                }
            },
            {
                "is_h265": 1,
                "play_addr": {
                    "url_key": "k1080h",
                    "url_list": ["https://v/1080h"],
                    "width": 1080,
                    "height": 1920,
                    "data_size": 5000,
                },
            },
        ],
    },
}

NOTE_AWEME = {
    "desc": "图集",
    "author": {"sec_uid": "MS4w", "nickname": "作者"},
    "music": {"play_url": {"url_list": ["https://sf3.douyinstatic.com/other.mp3"]}},
    "video": {"play_addr": {"url_list": ["https://v/silent.mp4"], "uri": "https://sf3.douyinstatic.com/bgm.mp3"}},
    "images": [
        {"url_list": ["https://p3.douyinpic.com/1.webp", "https://p3.douyinpic.com/1.jpeg"]},
        {
            "url_list": ["https://p3.douyinpic.com/2.jpeg"],
            "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/live2.mp4"]}},
        },
        {"url_list": []},
    ],
}


def _parse(monkeypatch, aweme, url=PC_URL):
    async def slides(self, video_id):
        assert video_id == VIDEO_ID
        return {"aweme_details": [aweme]}

    monkeypatch.setattr(DouYin, "_get_slides_info", slides)
    return asyncio.run(DouYin().parse_share_url(url))


def test_video(monkeypatch):
    info = _parse(monkeypatch, VIDEO_AWEME)
    assert info.video_url == "https://v3-web.douyinvod.com/play/720.mp4"
    assert (info.width, info.height, info.duration) == (720, 1280, 15.2)
    assert info.music_url == "https://sf3.douyinstatic.com/m.mp3"
    assert info.cover_url == "https://p3.douyinpic.com/c.jpeg?x=1"
    assert info.images == []
    assert [(f.label, f.url, f.filesize) for f in info.formats] == [
        ("1080p", "https://v3-web.douyinvod.com/play/1080.mp4", 9000),
        ("1080p H.265", "https://v/1080h", 5000),
    ]
    assert (info.author.uid, info.author.name, info.author.avatar) == (
        "MS4w",
        "作者",
        "https://p3.douyinpic.com/a.jpeg",
    )
    assert info.title == "一条视频"


def test_primary_track_keeps_its_actual_framerate_and_bitrate(monkeypatch):
    aweme = copy.deepcopy(VIDEO_AWEME)
    aweme["video"]["bit_rate"][0].update({"FPS": 60, "bit_rate": 8000000})
    info = _parse(monkeypatch, aweme)
    assert info.video_fps == 60 and info.video_bitrate == 8000000 and info.video_codec == "H.264"


def test_official_playback_can_recover_higher_quality_than_web_list(monkeypatch):
    from qiuyuantu.parser.quality import best_video_format

    aweme = copy.deepcopy(VIDEO_AWEME)
    aweme["video"].update({"width": 2160, "height": 3840, "play_addr": {"uri": "v" + "1" * 20}, "bit_rate": []})
    requested = []

    def response(request):
        assert request.method == "HEAD" and "cookie" not in request.headers
        ratio = request.url.params["ratio"]
        requested.append(ratio)
        return httpx.Response(302, headers={"Location": "https://cdn.test/" + ratio + ".mp4"})

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(response), **kwargs)

    async def probe(url, headers):
        assert "Cookie" not in headers
        width, height = (720, 1280) if "origin" in url else (1080, 1920)
        return FormatInfo(url=url, width=width, height=height, fps=30, label=f"{width}p")

    monkeypatch.setattr(douyin, "create_async_client", client)
    monkeypatch.setattr(douyin.mp4_probe, "probe", probe)
    info = _parse(monkeypatch, aweme)
    highest = best_video_format(info)
    assert set(requested) == {"2160p", "origin"}
    assert highest.label == "1080p" and highest.url.endswith("2160p.mp4")


def test_web_stream_gets_api_formats_before_official_playback_check(monkeypatch):
    from qiuyuantu.parser import deferred

    aweme = copy.deepcopy(VIDEO_AWEME)
    release = asyncio.Event()

    async def original(self, video):
        await release.wait()
        return [FormatInfo(url="https://cdn.test/2160p.mp4", width=2160, height=3840, label="2160p")]

    async def slides(self, video_id):
        return {"aweme_details": [aweme]}

    async def run():
        with deferred.collect() as pending:
            info = await DouYin().parse_share_url(PC_URL)
        base = [f.url for f in info.formats]
        release.set()
        await deferred.finish(pending, 1)
        return base, [f.url for f in info.formats]

    monkeypatch.setattr(DouYin, "_original_formats", original)
    monkeypatch.setattr(DouYin, "_get_slides_info", slides)
    base, enriched = asyncio.run(run())
    assert "https://cdn.test/2160p.mp4" not in base
    assert enriched[0] == "https://cdn.test/2160p.mp4" and enriched[1:] == base


def test_image_note_with_live_photo(monkeypatch):
    info = _parse(monkeypatch, NOTE_AWEME)
    assert info.video_url == "" and info.formats == []
    # 图集的背景音乐在 video.play_addr.uri 里，music.play_url 不是它
    assert info.music_url == "https://sf3.douyinstatic.com/bgm.mp3"
    assert [(i.url, i.live_photo_url) for i in info.images] == [
        ("https://p3.douyinpic.com/1.jpeg", ""),
        ("https://p3.douyinpic.com/2.jpeg", "https://v3-web.douyinvod.com/live2.mp4"),
    ]
    assert info.author.avatar == ""


def test_old_play_url_is_resolved_through_redirect(monkeypatch):
    aweme = {**VIDEO_AWEME, "video": {"play_addr": {"url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/?id=1"]}}}

    async def redirect(self, url):
        assert url == "https://aweme.snssdk.com/aweme/v1/play/?id=1"
        return "https://v3-web.douyinvod.com/real.mp4"

    monkeypatch.setattr(DouYin, "get_video_redirect_url", redirect)
    assert _parse(monkeypatch, aweme).video_url == "https://v3-web.douyinvod.com/real.mp4"


def test_filtered_note_falls_back_to_browser(monkeypatch):
    async def filtered(self, video_id):
        raise ParseError("login", "抖音 filter reason=4")

    async def browser(self, video_id):
        return "from-browser"

    monkeypatch.setattr(DouYin, "_get_slides_info", filtered)
    monkeypatch.setattr(DouYin, "_note_via_browser", browser)
    assert asyncio.run(DouYin().parse_share_url(PC_URL)) == "from-browser"


def test_filtered_and_no_browser_keeps_original_error(monkeypatch):
    async def filtered(self, video_id):
        raise ParseError("restricted", "作者设置了")

    async def no_browser(self, video_id):
        return None

    monkeypatch.setattr(DouYin, "_get_slides_info", filtered)
    monkeypatch.setattr(DouYin, "_note_via_browser", no_browser)
    with pytest.raises(ParseError) as exc:
        asyncio.run(DouYin().parse_share_url(PC_URL))
    assert exc.value.reason == "restricted"


@pytest.mark.parametrize(
    "url, message",
    [
        # 主页 / 搜索页不是作品：说清楚，别归成「已删除」
        ("https://www.douyin.com/user/abc", "不是单条作品的链接"),
        ("https://www.douyin.com/search/%E5%80%9F%E5%8F%B7", "不是单条作品的链接"),
        ("https://other.douyin.cc/x", "Douyin not support this host"),
    ],
)
def test_unusable_urls(url, message):
    with pytest.raises(Exception, match=message):
        asyncio.run(DouYin().parse_share_url(url))


@pytest.mark.parametrize(
    ("desc", "title"),
    [
        (
            "不好 是台风 - 今日有雪223于20260711发布在抖音，已经收获了2281.9万个喜欢，来抖音，记录美好生活！",
            "不好 是台风",
        ),
        ("正文里本来就有 - 横杠", "正文里本来就有 - 横杠"),
        ("页面上取到的正文 #话题", "页面上取到的正文 #话题"),
    ],
)
def test_note_title_drops_meta_description_tail(desc, title):
    assert douyin._note_title(desc) == title


_FULL = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oAD~tplv-dy-aweme-images:q75.webp?x=1"
_SMALL = "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oAD~tplv-dy-aweme-images-v2:1440:1922:q75.webp?x=1"


@pytest.mark.parametrize(
    ("pages", "renewed", "expected", "opened"),
    [
        ([{"images": [_SMALL]}, {"images": [_FULL]}], True, _FULL, 2),  # 换会话重抽到原尺寸
        ([{"images": [_SMALL]}, {"images": [_FULL]}], False, _SMALL, 1),  # 冷却期内不换，就用缩小版
        ([{"images": [_SMALL]}, {}], True, _SMALL, 2),  # 重抽那次页面出错，退回缩小版
        ([{"images": [_FULL]}], True, _FULL, 1),  # 本来就是原尺寸，不折腾
    ],
)
async def test_note_renews_session_when_images_are_downscaled(monkeypatch, pages, renewed, expected, opened):
    calls = []

    async def open_note(self, video_id, page_url, attempt):
        calls.append(attempt)
        return pages[attempt]

    async def renew():
        return renewed

    monkeypatch.setattr(DouYin, "_open_note", open_note)
    monkeypatch.setattr(douyin._warm_browser, "renew_context", renew)
    info = await DouYin()._note_via_browser("1")
    assert [i.url for i in info.images] == [expected] and len(calls) == opened


async def test_renew_context_respects_cooldown():
    browser = douyin._WarmBrowser(1)
    assert await browser.renew_context() is False  # 浏览器还没起来
    browser._browser = object()
    browser._renewed_at = douyin.time.monotonic()
    assert await browser.renew_context() is False  # 刚换过


NOTE_PC_URL = "https://www.douyin.com/note/7424432820954598707"


def _race(monkeypatch, slides, cookie=""):
    """跑一次图文解析，记下浏览器被调了几次、最后有没有被取消。"""
    calls = {"browser": 0, "cancelled": False}

    async def browser(self, video_id):
        calls["browser"] += 1
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            calls["cancelled"] = True
            raise
        return "from-browser"

    async def slides_info(self, video_id):
        await asyncio.sleep(0.01)
        return slides()

    monkeypatch.setattr(DouYin, "_get_slides_info", slides_info)
    monkeypatch.setattr(DouYin, "_note_via_browser", browser)
    monkeypatch.setattr(douyin, "_configured_cookie", lambda: cookie)

    async def run():
        result = await DouYin().parse_share_url(NOTE_PC_URL)
        await asyncio.sleep(0.1)  # 给被取消的任务跑完收尾
        return result

    return asyncio.run(run()), calls


def test_note_starts_browser_alongside_api(monkeypatch):
    def filtered():
        raise ParseError("login", "抖音 filter reason=4")

    result, calls = _race(monkeypatch, filtered)
    assert result == "from-browser" and calls["browser"] == 1  # 用的是提前开的那次，没再开第二次


def test_note_api_data_wins_and_browser_is_cancelled(monkeypatch):
    async def build(self, aweme):
        return "from-api"

    monkeypatch.setattr(DouYin, "_build", build)
    result, calls = _race(monkeypatch, lambda: {"aweme_details": [{"aweme_id": "1"}]})
    assert result == "from-api" and calls["cancelled"]


def test_note_with_login_cookie_does_not_race(monkeypatch):
    # 配了登录 cookie 接口能拿到图文，别白开浏览器
    async def build(self, aweme):
        return "from-api"

    monkeypatch.setattr(DouYin, "_build", build)
    result, calls = _race(monkeypatch, lambda: {"aweme_details": [{"aweme_id": "1"}]}, cookie="sessionid=x")
    assert result == "from-api" and calls["browser"] == 0


def test_page_images_prefers_complete_aweme_list():
    dom = {"images": ["https://p3/dom-a"], "ordered": ["https://p3/a.jpeg", "https://p3/b.jpeg"], "total": 2}
    assert douyin._page_images(dom) == ["https://p3/a.jpeg", "https://p3/b.jpeg"]
    # awemeInfo 还没凑齐：用 DOM 里的
    assert douyin._page_images({**dom, "ordered": ["https://p3/a.jpeg"]}) == ["https://p3/dom-a"]
    assert douyin._page_images({"images": ["https://p3/dom-a"], "total": None}) == ["https://p3/dom-a"]


class _FilteredClient:
    """slidesinfo 每次都回 filter_list 的假客户端。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **_kw):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"status_code": 0, "aweme_details": None, "filter_list": [{"reason": 8}]}

        return Resp()


@pytest.mark.parametrize(
    ("cookie", "note", "browser", "reason", "says_login"),
    [
        ("", False, True, "restricted", True),  # 匿名视频：说清楚要登录、重试没用，不再只报 reason 码
        ("", True, True, "login", False),  # 匿名图文：报 login 交给浏览器兜底
        ("sessionid=x", False, True, "restricted", False),  # 带了登录还被 filter：真是作者限制
    ],
)
def test_filtered_slides_message(monkeypatch, cookie, note, browser, reason, says_login):
    monkeypatch.setenv("PARSE_VIDEO_DOUYIN_COOKIE", cookie)
    monkeypatch.setattr(douyin, "_browser_available", lambda: browser)
    monkeypatch.setattr(douyin, "create_async_client", lambda **_kw: _FilteredClient())
    parser = DouYin()
    parser._note = note
    with pytest.raises(ParseError) as exc:
        asyncio.run(parser._get_slides_info(VIDEO_ID))
    assert exc.value.reason == reason
    assert ("只给登录用户看" in str(exc.value)) is says_login


def test_note_data_read_straight_from_html():
    """图文页 HTML 的 RSC 载荷里就有整条作品，不用等页面渲染（2026-10-14：~3.1 秒降到 ~1.8 秒）。
    推荐区别的作品也有 awemeId，要认准这一条；验证页（几 KB）直接返回 None 交给渲染那条路。"""
    import json

    def push(obj):
        payload = "5:" + json.dumps(obj, separators=(",", ":"), ensure_ascii=False)  # 和真实载荷一样紧凑
        return f"<script>self.__pace_f.push([1,{json.dumps(payload)}])</script>"

    note = {
        "awemeId": VIDEO_ID,
        "desc": "不好 是台风",
        "authorInfo": {"nickname": "今日有雪223", "secUid": "MS4w"},
        "music": {"playUrl": {"urlList": ["https://sf6/ies-music/1.mp3"]}},
        "images": [
            {
                "uri": "tos-cn-i-0813/abc",
                "urlList": ["//p3/tos-cn-i-0813/abc~q75.webp?x=1", "https://p3/tos-cn-i-0813/abc~q75.jpeg?x=1"],
                "video": {"playAddr": [{"src": "//v26/video/tos/live.mp4"}]},
            },
            {"uri": "tos-cn-i-0813/def", "urlList": ["https://p3/tos-cn-i-0813/def~q75.webp"]},
        ],
    }
    other = {"awemeId": "123", "images": [{"uri": "x", "urlList": ["https://p3/x.jpeg"]}]}
    html = push({"list": [other]}) + push({"page": {"aweme": note}}) + "x" * douyin._MIN_NOTE_HTML

    dom = douyin.note_from_html(html, VIDEO_ID)
    assert dom["total"] == 2 and dom["author"] == "今日有雪223" and dom["music"] == "https://sf6/ies-music/1.mp3"
    images = douyin._note_images(dom)
    assert [(i.url, i.live_photo_url) for i in images] == [
        ("https://p3/tos-cn-i-0813/abc~q75.jpeg?x=1", "https://v26/video/tos/live.mp4"),  # JPEG 优先、补上 https:
        ("https://p3/tos-cn-i-0813/def~q75.webp", ""),
    ]
    assert douyin.note_from_html("<html>人机验证</html>", VIDEO_ID) is None


@pytest.mark.parametrize(
    ("dropped", "browser_delay", "url"),
    [
        (True, 0, PC_URL),
        (True, 1, PC_URL),
        (False, 0, PC_URL),
        # 图文链接（浏览器已经同时开跑）、接口一个实况都没给、也没留线索：照样等页面核对（2026-10-16）
        ("silent", 0, NOTE_PC_URL),
    ],
)
def test_api_note_missing_live_is_filled_from_page(monkeypatch, dropped, browser_delay, url):
    """匿名接口给了图文却丢了一种实况（记录里有视频尺寸、没给地址）：等页面那边补上（按图片 id 对，签名不同）；
    页面等太久就照旧用接口的；没丢的图文根本不等页面。"""
    monkeypatch.setattr(douyin, "_LIVE_FILL_WAIT", 0.2)
    calls = []
    aweme = json.loads(json.dumps(NOTE_AWEME))
    if dropped is True:
        aweme["images"][0]["resolution_log_param"] = "{'image_publish_width': 2160, 'video_publish_width': 1308}"
    if dropped == "silent":
        del aweme["images"][1]["video"]  # 接口一个实况都没给

    async def page(self, video_id):
        calls.append(video_id)
        await asyncio.sleep(browser_delay)
        return VideoInfo(
            title="",
            video_url="",
            cover_url="",
            images=[
                ImgInfo(
                    url="https://p3-pc-sign.douyinpic.com/1~tplv-q75.jpeg?sig=x", live_photo_url="https://v/live1.mp4"
                ),
                ImgInfo(
                    url="https://p3-pc-sign.douyinpic.com/2~tplv-q75.jpeg?sig=y", live_photo_url="https://v/other.mp4"
                ),
            ],
        )

    monkeypatch.setattr(DouYin, "_note_via_browser", page)
    monkeypatch.setattr(douyin, "_configured_cookie", lambda: "")
    info = _parse(monkeypatch, aweme, url)
    lives = [i.live_photo_url for i in info.images]
    if dropped == "silent":
        assert lives == ["https://v/live1.mp4", "https://v/other.mp4"]
        return
    if not dropped:
        assert calls == [] and lives == ["", "https://v3-web.douyinvod.com/live2.mp4"]
    elif browser_delay:
        assert lives == ["", "https://v3-web.douyinvod.com/live2.mp4"]  # 等不到，和以前一样
    else:
        # 第一张接口没给实况、页面有，补上；第二张接口已经有的不覆盖
        assert lives == ["https://v/live1.mp4", "https://v3-web.douyinvod.com/live2.mp4"]


@pytest.mark.parametrize(
    ("dom", "message"),
    [({"video": True}, "只给登录用户看"), ({"gone": True, "images": []}, "没有公开给未登录用户")],
)
def test_browser_verdict_replaces_cookie_hint_for_filtered_note(monkeypatch, dom, message):
    async def filtered(self, video_id):
        raise ParseError("login", "抖音 filter reason=1")

    async def open_note(self, video_id, page_url, attempt):
        return dom

    monkeypatch.setattr(DouYin, "_get_slides_info", filtered)
    monkeypatch.setattr(DouYin, "_open_note", open_note)
    with pytest.raises(ParseError) as exc:
        asyncio.run(DouYin().parse_share_url("https://www.douyin.com/note/" + VIDEO_ID))
    assert exc.value.reason == "restricted" and message in str(exc.value)
    assert "cookies" not in str(exc.value)


@pytest.mark.parametrize(("browser", "reason"), [(True, "blocked"), (False, "login")])
def test_inconclusive_browser_asks_to_retry_and_only_selfhost_without_browser_needs_cookie(
    monkeypatch, browser, reason
):
    async def filtered(self, video_id):
        raise ParseError("login", "抖音 filter reason=1")

    async def nothing(self, video_id):
        return None

    monkeypatch.setattr(DouYin, "_get_slides_info", filtered)
    monkeypatch.setattr(DouYin, "_note_via_browser", nothing)
    monkeypatch.setattr(douyin, "_browser_available", lambda: browser)
    with pytest.raises(ParseError) as exc:
        asyncio.run(DouYin().parse_share_url("https://www.douyin.com/note/" + VIDEO_ID))
    assert exc.value.reason == reason
    assert ("filter reason" in str(exc.value)) is (not browser)


def test_login_only_video_keeps_its_message_when_note_page_is_empty(monkeypatch):
    async def filtered(self, video_id):
        raise ParseError("restricted", douyin._LOGIN_ONLY_VIDEO)

    async def open_note(self, video_id, page_url, attempt):
        return {"gone": True, "images": []}

    monkeypatch.setattr(DouYin, "_get_slides_info", filtered)
    monkeypatch.setattr(DouYin, "_open_note", open_note)
    with pytest.raises(ParseError) as exc:
        asyncio.run(DouYin().parse_share_url(PC_URL))
    assert "只给登录用户看" in str(exc.value)
