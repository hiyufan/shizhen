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
