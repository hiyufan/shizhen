"""小红书解析：笔记数据、接口重试、链接签名与笔记删除判定。"""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from qiuyuantu.parser import redbook
from qiuyuantu.parser.errors import classify
from qiuyuantu.parser.redbook import RedBook


def _h264(w, h, url, **kw):
    return {"width": w, "height": h, "masterUrl": url, **kw}


VIDEO_NOTE = {
    "title": "",
    "desc": "视频笔记" * 20,
    "user": {"userId": "u1", "nickname": "作者", "avatar": "a.jpg"},
    "imageList": [{"urlDefault": "http://sns-webpic-qc.xhscdn.com/2024/abc/cover!nd_dft"}],
    "video": {
        "capa": {"duration": 33},
        "media": {
            "stream": {
                "h264": [
                    _h264(720, 1280, "v720", size=10, videoBitrate=1),
                    _h264(1080, 1920, "v1080a", duration=12000, videoBitrate=1),
                    _h264(1080, 1920, "v1080b", duration=12000, videoBitrate=9),
                    _h264(720, 1280, "v720dup"),
                    {"width": 1, "height": 1},
                ],
                "h265": [{"width": 1080, "height": 1920}, _h264(1080, 1920, "v265", size=7)],
            }
        },
    },
}

IMAGE_NOTE = {
    "title": "图文",
    "user": {"userId": "u2", "nickName": "昵称"},
    "imageList": [
        {"urlDefault": "http://sns-webpic-qc.xhscdn.com/2024/abc/notes_pre_post/tok1!nd_dft_wlteh_jpg_3"},
        {
            "fileId": "fid2",
            "urlDefault": "http://x/y/z",
            "livePhoto": True,
            "stream": {"h264": [{"masterUrl": "live2"}]},
        },
        {"urlDefault": "http://a/b"},
        {"livePhoto": True},
    ],
}


def _build(data, key="urlDefault", nick="nickname"):
    return RedBook()._build(data, image_url_key=key, nick_key=nick)


def test_video_note_picks_largest_h264_and_lists_other_tracks():
    info = _build(VIDEO_NOTE)
    assert info.video_url == "v1080b"
    assert (info.width, info.height, info.duration) == (1080, 1920, 12.0)
    assert [(f.label, f.url, f.codec) for f in info.formats] == [("720p", "v720", ""), ("1080p H.265", "v265", "H.265")]
    assert info.images == []
    assert info.cover_url == "http://sns-webpic-qc.xhscdn.com/2024/abc/cover!nd_dft"
    assert info.title == ("视频笔记" * 20)[:60]
    assert (info.author.uid, info.author.name, info.author.avatar) == ("u1", "作者", "a.jpg")


def test_image_note_uses_original_images_and_live_photos():
    info = _build(IMAGE_NOTE, key="url", nick="nickName")
    assert info.video_url == "" and info.formats == []
    assert [(i.url, i.live_photo_url) for i in info.images] == [
        ("https://ci.xiaohongshu.com/notes_pre_post/tok1?imageView2/2/w/0/format/jpg/q/90", ""),
        ("https://ci.xiaohongshu.com/fid2?imageView2/2/w/0/format/jpg/q/90", "live2"),
        ("http://a/b", ""),
    ]
    assert info.author.name == "昵称"


def test_video_duration_falls_back_to_capa():
    data = {**VIDEO_NOTE, "video": {"capa": {"duration": 33}, "media": {"stream": {"h264": [_h264(1, 2, "v")]}}}}
    assert _build(data).duration == 33.0


# ---------------------------------------------------------------- 换路 / 重试
SHARE = "https://xhslink.cn/o/abc"
NOTE_PAGE = (
    "https://www.xiaohongshu.com/discovery/item/n1?xsec_token=t&xsec_source=app_share",
    "<title>笔记 - 小红书</title><script>window.__INITIAL_STATE__="
    + json.dumps({"noteData": {"data": {"noteData": IMAGE_NOTE}}})
    + "</script>",
)
# 分享链接跳过去的地址里本来就有 xsec_token, 不能因为它就当成 404
EMPTY_PAGE = (NOTE_PAGE[0], '<title>小红书</title><script>window.__INITIAL_STATE__={"noteData":{}}</script>')
GONE_PAGE = ("https://www.xiaohongshu.com/404/sec_Ab12?source=xhs_sec_server", "<title>小红书</title>")
LOGIN_PAGE = ("https://www.xiaohongshu.com/login?redirectPath=x", "<title>登录</title>")


class _Pages:
    """按 UA 区分手机 / 桌面, 依次返回事先排好的页面。"""

    def __init__(self, mobile=(), desktop=()):
        self.pages = {"mobile": list(mobile), "desktop": list(desktop)}
        self.calls = []

    def client(self, **kw):
        pages = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, headers):
                mode = "mobile" if "iPhone" in headers["User-Agent"] else "desktop"
                pages.calls.append(mode)
                final_url, text = pages.pages[mode].pop(0)
                return SimpleNamespace(url=final_url, text=text, raise_for_status=lambda: None)

        return _Client()


def _parse(monkeypatch, pages, *, mobile_first):
    monkeypatch.setattr(redbook, "create_async_client", pages.client)
    monkeypatch.setattr(RedBook, "_prefer_mobile", False)
    monkeypatch.setattr(RedBook, "_mobile_first", staticmethod(lambda: mobile_first))
    return asyncio.run(RedBook().parse_share_url(SHARE))


def test_transient_404_is_retried_on_mobile_when_desktop_is_a_login_wall(monkeypatch, caplog):
    pages = _Pages(mobile=[GONE_PAGE, NOTE_PAGE])
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        info = _parse(monkeypatch, pages, mobile_first=True)
    assert info.title == "图文"
    assert pages.calls == ["mobile", "mobile"]  # 桌面页在这个出口上只会是登录墙, 不去白跑
    assert "跳到 https://www.xiaohongshu.com/404/sec_Ab12?source=xhs_sec_server 标题 '小红书'" in caplog.text
    assert "小红书手机页重试后解析成功" in caplog.text


def test_404_twice_is_reported_as_deleted(monkeypatch):
    pages = _Pages(mobile=[GONE_PAGE, GONE_PAGE])
    with pytest.raises(ValueError) as e:
        _parse(monkeypatch, pages, mobile_first=True)
    assert classify(e.value).reason == "deleted"
    assert pages.calls == ["mobile", "mobile"]


def test_share_link_token_is_not_mistaken_for_404(monkeypatch):
    pages = _Pages(mobile=[EMPTY_PAGE, EMPTY_PAGE])
    with pytest.raises(ValueError) as e:
        _parse(monkeypatch, pages, mobile_first=True)
    assert classify(e.value).reason != "deleted"
    assert "意外页面" in str(e.value)


def test_desktop_404_is_confirmed_on_mobile(monkeypatch):
    pages = _Pages(mobile=[NOTE_PAGE], desktop=[GONE_PAGE])
    assert _parse(monkeypatch, pages, mobile_first=False).title == "图文"
    assert pages.calls == ["desktop", "mobile"]


def test_mobile_error_wins_over_desktop_login_wall(monkeypatch):
    # 桌面页先撞登录墙、手机页拿到验证页: 报验证页, 登录墙是意料之中的
    pages = _Pages(mobile=[(NOTE_PAGE[0], "<title>安全验证</title>")], desktop=[LOGIN_PAGE])
    with pytest.raises(ValueError, match="验证页"):
        _parse(monkeypatch, pages, mobile_first=False)
    assert pages.calls == ["desktop", "mobile"]
    assert RedBook._prefer_mobile is True


def test_page_gone_title_is_reported_as_deleted(monkeypatch):
    gone = ("https://www.xiaohongshu.com/search_result/n1?xsec_token=t", "<title>你访问的页面不见了</title>")
    with pytest.raises(ValueError) as e:
        _parse(monkeypatch, _Pages(mobile=[gone, gone]), mobile_first=True)
    assert classify(e.value).reason == "deleted"
