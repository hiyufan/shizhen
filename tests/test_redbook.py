"""小红书解析：笔记数据、接口重试、链接签名与笔记删除判定。"""

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from qiuyuantu.parser import redbook
from qiuyuantu.parser.errors import ParseError, classify
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
        ("https://ci.xiaohongshu.com/notes_pre_post/tok1", ""),
        ("https://ci.xiaohongshu.com/fid2", "live2"),
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
SECURITY_PAGE = ("https://www.xiaohongshu.com/404/sec_Ab12?source=xhs_sec_server", "<title>小红书</title>")
GONE_PAGE = ("https://www.xiaohongshu.com/404", "<title>小红书</title>")
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
    pages = _Pages(mobile=[SECURITY_PAGE, NOTE_PAGE])
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


def test_security_redirect_remains_unknown_instead_of_claiming_deleted(monkeypatch):
    pages = _Pages(mobile=[SECURITY_PAGE, SECURITY_PAGE])
    with pytest.raises(ParseError) as exc:
        _parse(monkeypatch, pages, mobile_first=True)
    assert exc.value.reason == "restricted" and "无法确认" in exc.value.detail
    assert pages.calls == ["mobile", "mobile"]


def test_security_redirect_takes_priority_over_another_route_404(monkeypatch):
    pages = _Pages(desktop=[GONE_PAGE], mobile=[SECURITY_PAGE])
    with pytest.raises(ParseError) as exc:
        _parse(monkeypatch, pages, mobile_first=False)
    assert exc.value.reason == "restricted"
    assert pages.calls == ["desktop", "mobile"]


@pytest.mark.parametrize("mobile_first", [True, False])
@pytest.mark.parametrize(
    "page",
    [
        (SECURITY_PAGE[0] + "&code=300013", "<title>小红书</title>"),
        ("https://www.xiaohongshu.com/website-login/error?error_code=300013", "<title>小红书</title>"),
        (SECURITY_PAGE[0], "<h1>安全限制</h1><p>访问频繁，请稍后再试</p><span>300013</span>"),
        (SECURITY_PAGE[0], '<script>window.__INITIAL_STATE__={"noteData":{"data":{}}}</script><p>安全限制 300013</p>'),
        (
            "https://www.xiaohongshu.com/explore/n1",
            '<script>window.__INITIAL_STATE__={"note":{'
            '"currentNoteId":undefined,"noteDetailMap":{}}}</script><p>访问频繁 300013</p>',
        ),
    ],
)
def test_explicit_frequency_limit_is_blocked_and_never_retried(monkeypatch, mobile_first, page):
    mode = "mobile" if mobile_first else "desktop"
    pages = _Pages(**{mode: [page]})
    with pytest.raises(ParseError) as exc:
        _parse(monkeypatch, pages, mobile_first=mobile_first)
    assert exc.value.reason == "blocked" and "300013" in exc.value.detail
    assert pages.calls == [mode]


def test_normal_note_mentioning_security_error_is_still_parsed(monkeypatch):
    page = (NOTE_PAGE[0], NOTE_PAGE[1] + "<p>安全限制 300013 解决方法</p>")
    assert _parse(monkeypatch, _Pages(mobile=[page]), mobile_first=True).title == "图文"


def test_login_error_route_falls_back_to_mobile(monkeypatch):
    login = ("https://www.xiaohongshu.com/website-login/error", "<title>小红书</title>")
    pages = _Pages(desktop=[login], mobile=[NOTE_PAGE])
    assert _parse(monkeypatch, pages, mobile_first=False).title == "图文"
    assert pages.calls == ["desktop", "mobile"]
    assert RedBook._prefer_mobile is True


def test_note_or_token_numbers_are_not_error_codes():
    assert not redbook._security_limit("https://www.xiaohongshu.com/explore/300013?xsec_token=300013", "")
    assert not redbook._security_limit("https://www.xiaohongshu.com/404?originalUrl=https://x.test/?code=300013", "")
    assert not redbook._security_limit("https://www.xiaohongshu.com/404", "<p>安全限制 13000130</p>")


NOTE_ID = "650ef945000000001f005918"
OLD_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=AB_test%2B%3D&xsec_source=pc_user"
INTERNATIONAL_URL = OLD_URL.replace("www.xiaohongshu.com", "www.rednote.com")


def _international_page(note_id=NOTE_ID, **changes):
    note = {**IMAGE_NOTE, "noteId": note_id, **changes}
    return "<script>window.__INITIAL_STATE__=" + json.dumps({"noteData": {"data": {"noteData": note}}}) + "</script>"


async def _request_parse(monkeypatch, url, handler, *, cookie="", mobile_first=False, instance=None):
    monkeypatch.setenv("PARSE_VIDEO_XHS_COOKIE", cookie)
    monkeypatch.setattr(RedBook, "_prefer_mobile", False)
    monkeypatch.setattr(RedBook, "_mobile_first", staticmethod(lambda: mobile_first))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:

        class _Client:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(redbook, "create_async_client", lambda **kwargs: _Client())
        return await (instance or RedBook()).parse_share_url(url)


async def test_old_long_link_uses_official_mobile_entry_and_preserves_token(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=_international_page())

    info = await _request_parse(monkeypatch, OLD_URL, handler)
    assert len(calls) == 1
    assert str(calls[0].url) == INTERNATIONAL_URL
    assert "iPhone" in calls[0].headers["user-agent"]
    assert "cookie" not in calls[0].headers
    assert info.page_url == INTERNATIONAL_URL
    assert info.title == IMAGE_NOTE["title"] and len(info.images) == 3
    assert info.images[1].live_photo_url == "live2"


async def test_short_link_can_recover_from_long_link_in_redirect_history(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "xhslink.cn":
            return httpx.Response(302, headers={"Location": OLD_URL})
        if request.url.host == "www.rednote.com":
            return httpx.Response(200, text=_international_page())
        if request.url.path.startswith("/explore/"):
            return httpx.Response(302, headers={"Location": SECURITY_PAGE[0]})
        return httpx.Response(200, text=SECURITY_PAGE[1])

    info = await _request_parse(monkeypatch, SHARE, handler, mobile_first=True)
    assert info.title == IMAGE_NOTE["title"]
    assert calls == [SHARE, OLD_URL, SECURITY_PAGE[0], INTERNATIONAL_URL]


async def test_short_link_cannot_replace_requested_note_with_redirected_recommendation(monkeypatch):
    another = "688758420000000025019653"

    def handler(request):
        if request.url.host == "xhslink.cn":
            return httpx.Response(302, headers={"Location": OLD_URL})
        if request.url.path.endswith(NOTE_ID):
            return httpx.Response(302, headers={"Location": OLD_URL.replace(NOTE_ID, another)})
        return httpx.Response(200, text=_international_page(another))

    with pytest.raises(ParseError, match="笔记与请求不一致"):
        await _request_parse(monkeypatch, SHARE, handler, mobile_first=True)


@pytest.mark.parametrize("returned_id", ["688758420000000025019653", None])
async def test_international_entry_rejects_other_or_missing_note_id(monkeypatch, returned_id):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "www.rednote.com":
            return httpx.Response(200, text=_international_page(returned_id))
        return httpx.Response(200, text=SECURITY_PAGE[1], headers={})

    with pytest.raises(ParseError, match="笔记与请求不一致"):
        await _request_parse(monkeypatch, OLD_URL, handler)
    assert calls == [INTERNATIONAL_URL]


async def test_international_video_keeps_direct_formats(monkeypatch):
    def handler(request):
        return httpx.Response(200, text=_international_page(**VIDEO_NOTE))

    info = await _request_parse(monkeypatch, OLD_URL, handler)
    assert info.video_url == "v1080b" and info.duration == 12
    assert [f.url for f in info.formats] == ["v720", "v265"]


async def test_reusing_parser_does_not_reuse_previous_note_or_route(monkeypatch):
    calls = []
    instance = RedBook()
    another = "688758420000000025019653"

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=_international_page(request.url.path.rsplit("/", 1)[-1]))

    await _request_parse(monkeypatch, OLD_URL, handler, instance=instance)
    await _request_parse(monkeypatch, OLD_URL.replace(NOTE_ID, another), handler, instance=instance)
    assert calls == [INTERNATIONAL_URL, INTERNATIONAL_URL.replace(NOTE_ID, another)]


async def test_international_request_never_receives_configured_xhs_cookie(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=_international_page())

    await _request_parse(monkeypatch, INTERNATIONAL_URL, handler, cookie="web_session=private")
    assert len(calls) == 1 and "cookie" not in calls[0].headers


async def test_configured_xhs_cookie_keeps_original_desktop_entry(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        state = {"note": {"currentNoteId": NOTE_ID, "noteDetailMap": {NOTE_ID: {"note": IMAGE_NOTE}}}}
        return httpx.Response(200, text="<script>window.__INITIAL_STATE__=" + json.dumps(state) + "</script>")

    await _request_parse(monkeypatch, OLD_URL, handler, cookie="web_session=private")
    assert len(calls) == 1 and str(calls[0].url) == OLD_URL
    assert calls[0].headers["cookie"] == "web_session=private"
    assert "iPhone" not in calls[0].headers["user-agent"]


async def test_international_failure_can_use_original_mobile_entry_once(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "www.rednote.com":
            return httpx.Response(200, text=EMPTY_PAGE[1])
        return httpx.Response(200, text=_international_page())

    assert (await _request_parse(monkeypatch, OLD_URL, handler)).title == IMAGE_NOTE["title"]
    assert calls == [INTERNATIONAL_URL, OLD_URL]


@pytest.mark.parametrize("error", [503, 504, "transport"])
async def test_temporary_network_failure_retries_same_international_entry(monkeypatch, error):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            if error == "transport":
                raise httpx.TransportError("temporary relay failure", request=request)
            return httpx.Response(error)
        return httpx.Response(200, text=_international_page())

    info = await _request_parse(monkeypatch, OLD_URL, handler)
    assert info.title == IMAGE_NOTE["title"]
    assert calls == [INTERNATIONAL_URL, INTERNATIONAL_URL]


@pytest.mark.parametrize("code", ["300012", "300013"])
async def test_international_security_limit_is_not_retried(monkeypatch, code):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=f"<title>安全限制</title><p>{code}</p>")

    with pytest.raises(ParseError) as exc:
        await _request_parse(monkeypatch, OLD_URL, handler)
    assert exc.value.reason == "blocked" and code in exc.value.detail
    assert calls == [INTERNATIONAL_URL]


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.xiaohongshu.com.evil.test/explore/{NOTE_ID}",
        f"https://www.xiaohongshu.com@evil.test/explore/{NOTE_ID}",
        f"https://private@www.xiaohongshu.com/explore/{NOTE_ID}",
        f"https://www.xiaohongshu.com:22/explore/{NOTE_ID}",
        f"https://www.xiaohongshu.com/user/profile/{NOTE_ID}",
    ],
)
def test_international_entry_only_rewrites_official_note_urls(monkeypatch, url):
    monkeypatch.delenv("PARSE_VIDEO_XHS_COOKIE", raising=False)
    assert not redbook._international_note_url(url)


def test_rednote_links_use_the_xiaohongshu_parser():
    from qiuyuantu.parser import VideoSource, detect_source

    assert detect_source(INTERNATIONAL_URL) == VideoSource.RedBook
    assert detect_source(INTERNATIONAL_URL.replace("www.rednote.com", "rednote.com")) == VideoSource.RedBook
    assert detect_source(INTERNATIONAL_URL.replace("www.rednote.com", "rednote.com.evil.test")) != VideoSource.RedBook
