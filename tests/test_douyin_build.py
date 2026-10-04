"""抖音：slidesinfo 返回的作品数据 -> VideoInfo。接口换成假的，不碰网络。"""

import asyncio

import pytest

from parse_video_py.parser import douyin
from parse_video_py.parser.douyin import DouYin
from parse_video_py.parser.errors import ParseError

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
        ("https://www.douyin.com/user/abc", "Failed to parse video ID from PC share URL"),
        ("https://other.douyin.cc/x", "Douyin not support this host"),
    ],
)
def test_unusable_urls(url, message):
    with pytest.raises(ValueError, match=message):
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
