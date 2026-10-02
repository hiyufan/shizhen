"""快手落地页 INIT_STATE → VideoInfo：视频、多图图集、单图作品。数据按真实页面裁剪，不碰网络。"""

import asyncio

import httpx
import pytest

from parse_video_py.parser import kuaishou
from parse_video_py.parser.errors import ParseError
from parse_video_py.parser.kuaishou import KuaiShou


def _state(photo):
    return {"tmp_abc": {"result": 1, "photo": photo}}


COVER = "http://ws2.a.kwimgs.com/upic/2026/10/01/16/BMj_cover.jpg?clientCacheKey=3x8j.jpg"


def test_single_picture_post_returns_its_image_and_music():
    # v.kuaishou.com/n86DkRPN：photoType SINGLE_PICTURE，mainMvUrls 为空、没有 atlas，以前报「没拿到任何视频或图片」
    info = KuaiShou._build(
        _state(
            {
                "photoType": "SINGLE_PICTURE",
                "singlePicture": True,
                "mainMvUrls": [],
                "caption": "单图",
                "coverUrls": [{"cdn": "ws2.a.kwimgs.com", "url": COVER}],
                "width": 720,
                "height": 1280,
                "ext_params": {
                    "single": {
                        "music": "/ufile/atlas/x.m4a",
                        "musicCdnList": [{"cdn": "txmov2.a.kwimgs.com"}],
                        "cdnList": [{"cdn": "ws2.a.kwimgs.com"}],
                    }
                },
            }
        )
    )
    assert [i.url for i in info.images] == [COVER.replace("http://", "https://")]
    assert info.video_url == ""
    assert info.music_url == "https://txmov2.a.kwimgs.com/ufile/atlas/x.m4a"


@pytest.mark.parametrize(
    "caption, noticed",
    [("好看的晚霞和你一样温柔 #实况 #实况照片 #live", True), ("单图", False), ("#LivePhoto", True)],
)
def test_live_photo_post_says_the_moving_part_is_not_available(caption, noticed):
    # v.kuaishou.com/nnjvZU73：快手实况。分享页数据和普通单图一模一样（mtype 6、没有视频），只能从话题认出来
    photo = {
        "photoType": "SINGLE_PICTURE",
        "singlePicture": True,
        "mainMvUrls": [],
        "caption": caption,
        "coverUrls": [{"url": COVER}],
        "ext_params": {"mtype": 6, "single": {"type": 3}},
    }
    info = KuaiShou._build(_state(photo))
    assert len(info.images) == 1 and info.video_url == ""
    assert (info.notice == kuaishou.LIVE_NOTICE) is noticed


def test_video_with_live_in_title_gets_no_notice():
    photo = {"photoType": "VIDEO", "caption": "#live 现场", "mainMvUrls": [{"url": "https://v.kwaicdn.com/1.mp4"}]}
    assert KuaiShou._build(_state(photo)).notice == ""


def test_atlas_keeps_images_and_picks_up_music():
    info = KuaiShou._build(
        _state(
            {
                "mainMvUrls": [{"url": "https://v.kwaicdn.com/shell.mp4"}],
                "coverUrls": [{"url": COVER}],
                "ext_params": {
                    "atlas": {
                        "cdn": ["p2.a.yximgs.com"],
                        # v.kuaishou.com/nLZfVo2w（11 张横版图集）：路径带前导 /，给的是 webp
                        "list": ["/ufile/atlas/1.webp", "/ufile/atlas/2.jpg"],
                        "music": "/ufile/atlas/bgm.m4a",
                        "musicCdnList": [{"cdn": "txmov2.a.kwimgs.com"}],
                    }
                },
            }
        )
    )
    assert len(info.images) == 2 and info.video_url == ""  # 图集的 mainMvUrls 是配乐视频壳
    assert [i.url for i in info.images] == [
        "https://p2.a.yximgs.com/ufile/atlas/1.jpg",
        "https://p2.a.yximgs.com/ufile/atlas/2.jpg",
    ]
    assert info.music_url.endswith("/ufile/atlas/bgm.m4a")


def test_plain_video_is_unchanged():
    info = KuaiShou._build(
        _state(
            {
                "photoType": "VIDEO",
                "mainMvUrls": [{"url": "https://v.kwaicdn.com/1.mp4"}],
                "coverUrls": [{"url": COVER}],
                "duration": 12000,
            }
        )
    )
    assert info.video_url == "https://v.kwaicdn.com/1.mp4" and info.images == [] and info.music_url == ""


def test_web_and_landing_links_are_routed_to_kuaishou_by_photo_id():

    from parse_video_py.parser import VideoSource, detect_source
    from parse_video_py.parser.kuaishou import _PHOTO_ID

    pid = "3x7ryeb59738de4"
    for url in (
        f"https://www.kuaishou.com/short-video/{pid}?authorId=x",
        f"https://live.kuaishou.com/u/mayang9yc9/{pid}",
        f"https://c.kuaishou.com/fw/photo/{pid}?fid=1&cc=share_copylink",
        f"https://v.m.chenzhongtech.com/fw/long-video/{pid}",
        f"https://m.gifshow.com/fw/photo/{pid}?cc=share_wxms",  # /f/ 短链跳转链上的一站
    ):
        assert detect_source(url) == VideoSource.KuaiShou, url
        assert _PHOTO_ID.search(url).group(1) == pid, url
    assert detect_source("https://v.kuaishou.com/JSZcf5hc") == VideoSource.KuaiShou
    assert _PHOTO_ID.search("https://www.kuaishou.com/new-reco") is None


class _Redirects:
    """假客户端：按地址回放 302；记下请求过的地址。"""

    def __init__(self, hops, seen):
        self.hops, self.seen = hops, seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, **kw):
        self.seen.append(url)
        location = self.hops.get(url)
        status = 302 if location else 200
        return httpx.Response(
            status, headers={"location": location} if location else {}, request=httpx.Request("GET", url)
        )


def _resolve(monkeypatch, url, hops):
    seen = []
    monkeypatch.setattr(kuaishou, "create_async_client", lambda **kw: _Redirects(hops, seen))
    return asyncio.run(KuaiShou()._photo_id(url, {})), seen


def test_web_short_link_is_followed_to_the_photo_id(monkeypatch):
    hops = {
        "https://www.kuaishou.com/f/X-abc": "https://www.kuaishou.com/short-video/3x7ryeb59738de4?fid=1",
    }
    photo_id, seen = _resolve(monkeypatch, "https://www.kuaishou.com/f/X-abc", hops)
    assert photo_id == "3x7ryeb59738de4" and seen == ["https://www.kuaishou.com/f/X-abc"]


def test_relative_and_multi_hop_redirects(monkeypatch):
    hops = {
        "https://www.kuaishou.com/f/X-abc": "/s/abc",
        "https://www.kuaishou.com/s/abc": "https://v.m.chenzhongtech.com/fw/photo/3xabcdefghij?x=1",
    }
    assert _resolve(monkeypatch, "https://www.kuaishou.com/f/X-abc", hops)[0] == "3xabcdefghij"


def test_link_with_photo_id_needs_no_request(monkeypatch):
    photo_id, seen = _resolve(monkeypatch, "https://www.kuaishou.com/short-video/3xabcdefghij", {})
    assert photo_id == "3xabcdefghij" and seen == []


def test_expired_short_link_says_so(monkeypatch):
    hops = {"https://www.kuaishou.com/f/X-old": "https://kuaishou.com/"}
    with pytest.raises(ParseError) as exc:
        _resolve(monkeypatch, "https://www.kuaishou.com/f/X-old", hops)
    assert exc.value.reason == "deleted"


def test_profile_link_is_unsupported_without_following(monkeypatch):
    seen = []
    monkeypatch.setattr(kuaishou, "create_async_client", lambda **kw: _Redirects({}, seen))
    with pytest.raises(ParseError) as exc:
        asyncio.run(KuaiShou()._photo_id("https://www.kuaishou.com/profile/3xuser", {}))
    assert exc.value.reason == "unsupported" and seen == []


def test_short_link_that_lands_elsewhere_is_reported_for_fixing(monkeypatch):
    # 跳到了既不是作品页也不是首页的地方：多半是快手改了跳转，归到「解析出错」，用户能反馈
    hops = {"https://www.kuaishou.com/f/X-new": "https://www.kuaishou.com/some-new-page/abc"}
    with pytest.raises(ParseError) as exc:
        _resolve(monkeypatch, "https://www.kuaishou.com/f/X-new", hops)
    assert exc.value.reason == "parse"
