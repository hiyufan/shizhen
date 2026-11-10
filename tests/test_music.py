"""音乐封面公开响应的离线回放；不请求歌曲、不依赖账号 Cookie（网易云游客身份也是替身）。"""

import asyncio
import base64

import httpx
import pytest

from qiuyuantu import parser, utils
from qiuyuantu.convert import net
from qiuyuantu.parser import base, netease_guest
from qiuyuantu.parser.base import FormatInfo, VideoSource
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.music import NetEaseMusic, QQMusic, netease_cover, netease_target, qq_cover, qq_target
from qiuyuantu.parser.quality import best_video_format

NET_COVER = "https://p1.music.126.net/hash/123.jpg"
NET_REGISTER = "https://music.163.com/weapi/register/anonimous"
NET_DYNAMIC = "https://music.163.com/api/songplay/dynamic-cover"
DCOVER = "http://dcover.music.126.net/a/b/c/cover.mp4?wsSecret=sig&wsTime=1"
QQ_COVER = "https://y.qq.com/music/photo_new/T002R300x300M000albumMID_1.jpg?max_age=2592000"


@pytest.fixture
def music_http(monkeypatch):
    routes, calls = {}, []

    def handle(request):
        url = str(request.url)
        calls.append(request)
        assert url in routes, f"意外的外网请求：{url}"
        reply = routes[url]
        if callable(reply):
            return reply(request)
        if isinstance(reply, dict):
            return httpx.Response(200, json=reply)
        if isinstance(reply, tuple):
            return httpx.Response(reply[0], headers=reply[1])
        return httpx.Response(200, text=reply)

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(base, "create_async_client", client)
    # 游客身份是进程级缓存，每个用例从头领；默认这首歌没有动态封面
    monkeypatch.setitem(netease_guest._guest_state, "cookie", "")
    routes[NET_REGISTER] = _guest_reply("guest-1")
    routes[NET_DYNAMIC] = {"code": 200, "data": {}}
    return routes, calls


def _guest_reply(value: str, code: int = 200):
    def reply(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        assert set(form) == {"params", "encSecKey"} and len(form["encSecKey"]) == 256  # 网页端加密格式，不是明文
        cookie = f"MUSIC_A={value}; Max-Age=2147483647; Path=/; Domain=.music.163.com; HTTPOnly"
        return httpx.Response(200, json={"code": code}, headers={"Set-Cookie": cookie})

    return reply


def test_guest_device_ids_avoid_checksums_the_platform_rejects():
    # 校验串里带 + 或 / 的设备号平台一律回 code 400（2026-11 实测 4/4 说中）
    for _ in range(200):
        device = netease_guest._device()
        assert len(device) == 52 and not {"+", "/"} & set(netease_guest._digest(device))
        name = base64.b64decode(netease_guest._username(device)).decode()
        assert name == f"{device} {netease_guest._digest(device)}"


def _dynamic_reply(seen: list, covers: dict[str, str], *, stale: str = ""):
    """按 songId 回动态封面；带着 stale 这个游客身份来问时按失效（301）处理。"""

    def reply(request):
        song = dict(httpx.QueryParams(request.content.decode()))["songId"]
        seen.append((song, request.headers.get("cookie")))
        if stale and request.headers.get("cookie") == stale:
            return httpx.Response(200, json={"code": 301, "message": "系统错误"})
        url = covers.get(song)
        return httpx.Response(200, json={"code": 200, "data": {"videoPlayUrl": url} if url else {}})

    return reply


@pytest.mark.parametrize(
    ("url", "target"),
    [
        ("https://music.163.com/playlist?id=14005122656&uct2=token", ("playlist", "14005122656")),
        ("https://music.163.com/#/playlist?id=14005122656", ("playlist", "14005122656")),
        ("https://y.music.163.com/m/song?id=186016&app_version=9", ("song", "186016")),
        ("https://music.163.com/m/album/18905/", ("album", "18905")),
    ],
)
def test_netease_url_forms(url, target):
    assert netease_target(url) == target
    assert parser.detect_source(url) == VideoSource.NetEaseMusic


@pytest.mark.parametrize(
    ("url", "target"),
    [
        ("https://y.qq.com/n/ryqq/playlist/3602407677?from=share", ("playlist", "3602407677")),
        ("https://y.qq.com/n/ryqq_v2/songDetail/songMID", ("song", "songMID")),
        ("https://y.qq.com/n/yqq/album/albumMID.html", ("album", "albumMID")),
        ("https://i.y.qq.com/n2/m/share/details/taoge.html?id=3602407677", ("playlist", "3602407677")),
        ("https://i.y.qq.com/n2/m/share/details/album.html?albummid=albumMID", ("album", "albumMID")),
        ("https://i.y.qq.com/n2/m/share/details/song.html?songmid=songMID", ("song", "songMID")),
        ("https://y.qq.com/n/yqq/song.html?songmid=songMID", ("song", "songMID")),
        ("https://i.y.qq.com/v8/playsong.html?songmid=songMID", ("song", "songMID")),
    ],
)
def test_qq_url_forms(url, target):
    assert qq_target(url) == target
    assert parser.detect_source(url) == VideoSource.QQMusic


@pytest.mark.parametrize(
    ("parse", "url"),
    [
        (netease_target, "https://music.163.com/playlist?id=1%26other%3D2"),
        (netease_target, "https://music.163.com/user/home?id=1"),
        (netease_target, "https://music.163.com/playlist"),
        (netease_target, "https://music.163.com.evil.test/playlist?id=1"),
        (netease_target, "ftp://music.163.com/playlist?id=1"),
        (qq_target, "https://y.qq.com/n/ryqq/playlist/not-a-number"),
        (qq_target, "https://y.qq.com/n/ryqq/songDetail/../secret"),
        (qq_target, "https://y.qq.com/n2/m/share/details/song.html?songmid=one%26two"),
        (qq_target, "https://y.qq.com/n/ryqq/profile"),
        (qq_target, "https://y.qq.com.evil.test/n/ryqq/playlist/1"),
    ],
)
def test_invalid_or_unrelated_music_links_are_unsupported(parse, url):
    with pytest.raises(ParseError) as exc:
        parse(url)
    assert exc.value.reason == "unsupported"


def test_cover_variants_keep_real_high_resolution_urls():
    assert netease_cover(NET_COVER.replace("https:", "http:") + "?param=200y200") == NET_COVER
    assert qq_cover(QQ_COVER) == "https://y.gtimg.cn/music/photo_new/T002R800x800M000albumMID_1.jpg"
    assert qq_cover("http://qpic.y.qq.com/music_cover/hash/300?n=1") == "https://qpic.y.qq.com/music_cover/hash/1000"
    assert qq_cover("https://qpic.y.qq.com/music_cover/hash/2000") == "https://qpic.y.qq.com/music_cover/hash/2000"


@pytest.mark.parametrize("normalizer", [netease_cover, qq_cover])
@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "https://example.com/logo.jpg", "http://127.0.0.1/x.jpg"])
def test_cover_must_be_a_platform_image(normalizer, url):
    with pytest.raises(ParseError) as exc:
        normalizer(url)
    assert exc.value.reason == "empty"


@pytest.mark.parametrize("kind", ["song", "album", "playlist"])
async def test_netease_returns_one_cover_and_never_song_audio(music_http, kind):
    routes, calls = music_http
    detail = {"name": "封面标题", "picUrl": NET_COVER + "?param=200y200", "coverImgUrl": NET_COVER}
    paths = {
        "song": "/api/song/detail?ids=%5B42%5D",
        "album": "/api/v1/album/42",
        "playlist": "/api/v6/playlist/detail?id=42&n=0",
    }
    data = {"code": 200, "songs": [dict(detail, album=detail)]} if kind == "song" else {"code": 200, kind: detail}
    routes["https://music.163.com" + paths[kind]] = data
    info = await parser.parse_video_share_url(f"https://music.163.com/{kind}?id=42&uct2=unused")
    assert info.title == "封面标题"
    assert info.cover_url == info.images[0].url == NET_COVER
    assert len(info.images) == 1 and info.source == "netease"
    assert not info.video_url and not info.music_url and not info.formats
    assert info.page_url == f"https://music.163.com/{kind}?id=42"
    assert calls[0].headers["referer"] == "https://music.163.com/"
    assert not calls[0].headers.get("cookie")
    # 歌曲另查动态封面（这首没有）；歌单没有动态封面，不领游客身份
    assert [str(c.url) for c in calls[1:]] == ([NET_REGISTER, NET_DYNAMIC] if kind == "song" else [])


async def test_netease_dynamic_cover_becomes_a_server_side_download(music_http):
    routes, calls = music_http
    seen = []
    routes["https://music.163.com/api/v1/album/42"] = {
        "code": 200,
        "album": {"name": "故事商铺", "picUrl": NET_COVER},
        "songs": [{"id": 7}, {"id": 8}],
    }
    routes[NET_DYNAMIC] = _dynamic_reply(seen, {"7": DCOVER})
    for _ in range(2):
        info = await parser.parse_video_share_url("https://music.163.com/album?id=42")
        # 地址签名很快过期，结果又会被缓存：只给服务端档位，下载时重新取
        assert info.formats == [FormatInfo(label="动态封面", format_spec="netease-artwork", ext="mp4")]
        assert info.images[0].url == NET_COVER and not info.video_url
        assert best_video_format(info) is None
    # 专辑问第一首歌；游客身份整个进程领一次，只带给动态封面接口
    assert seen == [("7", "MUSIC_A=guest-1")] * 2
    assert [str(c.url) for c in calls].count(NET_REGISTER) == 1
    assert not any(c.headers.get("cookie") for c in calls if str(c.url) != NET_DYNAMIC)


async def test_netease_renews_a_rejected_guest_identity_once(music_http):
    routes, calls = music_http
    seen = []
    netease_guest._guest_state["cookie"] = "MUSIC_A=old"
    routes[NET_REGISTER] = _guest_reply("guest-2")
    routes[NET_DYNAMIC] = _dynamic_reply(seen, {"42": DCOVER}, stale="MUSIC_A=old")
    assert await netease_guest.dynamic_cover(NetEaseMusic(), "42") == DCOVER
    assert seen == [("42", "MUSIC_A=old"), ("42", "MUSIC_A=guest-2")]
    assert netease_guest._guest_state["cookie"] == "MUSIC_A=guest-2"


@pytest.mark.parametrize(
    "failure",
    [
        {NET_REGISTER: _guest_reply("", code=400)},
        {NET_DYNAMIC: (500, {})},
        {NET_DYNAMIC: {"code": 200, "data": {"videoPlayUrl": "https://evil.test/a.mp4"}}},
        {NET_DYNAMIC: {"code": -460}},
    ],
    ids=["no-guest", "server-error", "foreign-url", "refused"],
)
async def test_netease_dynamic_cover_problems_still_return_the_static_cover(music_http, failure):
    routes, _ = music_http
    routes["https://music.163.com/api/song/detail?ids=%5B42%5D"] = {
        "code": 200,
        "songs": [{"name": "歌", "album": {"picUrl": NET_COVER}}],
    }
    routes.update(failure)
    info = await NetEaseMusic().parse_share_url("https://music.163.com/song?id=42")
    assert info.images[0].url == NET_COVER and not info.formats


async def test_netease_slow_dynamic_cover_does_not_hold_the_parse(music_http, monkeypatch):
    routes, _ = music_http
    routes["https://music.163.com/api/song/detail?ids=%5B42%5D"] = {
        "code": 200,
        "songs": [{"name": "歌", "album": {"picUrl": NET_COVER}}],
    }

    async def stuck(_parser, _song):
        await asyncio.sleep(60)

    monkeypatch.setattr(netease_guest, "dynamic_cover", stuck)
    monkeypatch.setattr("qiuyuantu.parser.music._DYNAMIC_TIMEOUT", 0.01)
    info = await NetEaseMusic().parse_share_url("https://music.163.com/song?id=42")
    assert info.images[0].url == NET_COVER and not info.formats


async def test_netease_download_reads_the_dynamic_cover_again(music_http):
    routes, _ = music_http
    seen = []
    routes["https://music.163.com/api/v1/album/42"] = {
        "code": 200,
        "album": {"picUrl": NET_COVER},
        "songs": [{"id": 9}],
    }
    routes[NET_DYNAMIC] = _dynamic_reply(seen, {"9": DCOVER})
    assert await NetEaseMusic().dynamic_cover("https://music.163.com/album?id=42") == DCOVER
    assert await NetEaseMusic().dynamic_cover("https://music.163.com/playlist?id=42") == ""
    assert seen == [("9", "MUSIC_A=guest-1")]


@pytest.mark.parametrize(("code", "reason"), [(404, "deleted"), (301, "login"), (-462, "blocked"), (500, "parse")])
async def test_netease_errors_have_meaningful_reasons(music_http, code, reason):
    routes, _ = music_http
    routes["https://music.163.com/api/v6/playlist/detail?id=42&n=0"] = {"code": code}
    with pytest.raises(ParseError) as exc:
        await NetEaseMusic().parse_share_url("https://music.163.com/playlist?id=42")
    assert exc.value.reason == reason


async def test_private_or_missing_song_is_not_an_empty_success(music_http):
    routes, _ = music_http
    routes["https://music.163.com/api/song/detail?ids=%5B42%5D"] = {"code": 200, "songs": []}
    with pytest.raises(ParseError) as exc:
        await NetEaseMusic().parse_video_id("42")
    assert exc.value.reason == "deleted"


@pytest.mark.parametrize("kind", ["songDetail", "albumDetail", "playlist"])
async def test_qq_uses_public_page_metadata(music_http, kind):
    routes, calls = music_http
    canonical = f"https://y.qq.com/n/ryqq_v2/{kind}/42"
    routes[canonical] = f'''<meta property="og:image" content="{QQ_COVER}">
      <meta property="og:title" content="歌单 &amp; 封面 - QQ音乐-品牌介绍">
      <meta property="og:music:artist" content="歌手">'''
    original = "https://y.gtimg.cn/music/photo_new/T002M000albumMID_1.jpg"
    routes[original] = (200, {"Content-Type": "image/jpeg"})
    info = await parser.parse_video_share_url(f"https://y.qq.com/n/ryqq/{kind}/42")
    assert info.title == "歌单 & 封面" and info.author.name == "歌手"
    assert info.images[0].url == original
    assert info.cover_url == info.images[0].url and info.source == "qqmusic"
    assert not info.video_url and not info.music_url and not info.formats
    assert len(calls) == 2 and calls[0].headers["referer"] == "https://y.qq.com/"
    assert calls[1].method == "HEAD"


async def test_qq_page_without_cover_does_not_return_site_logo(music_http):
    routes, _ = music_http
    routes["https://y.qq.com/n/ryqq_v2/playlist/42"] = '<meta property="og:title" content="QQ音乐">'
    with pytest.raises(ParseError) as exc:
        await QQMusic().parse_video_id("playlist:42")
    assert exc.value.reason == "empty"


async def test_qq_original_falls_back_only_to_verified_larger_cover(music_http):
    routes, calls = music_http
    stem = "https://y.gtimg.cn/music/photo_new/T002"
    routes[stem + "M000albumMID_1.jpg"] = (404, {})
    routes[stem + "R1500x1500M000albumMID_1.jpg"] = (200, {"Content-Type": "image/jpeg"})
    assert await QQMusic()._best_cover(QQ_COVER) == stem + "R1500x1500M000albumMID_1.jpg"
    assert all(call.method == "HEAD" for call in calls)


async def test_qq_missing_original_and_variants_keep_existing_cover(music_http):
    routes, _ = music_http
    stem = "https://y.gtimg.cn/music/photo_new/T002"
    routes[stem + "M000albumMID_1.jpg"] = (404, {})
    routes[stem + "R1500x1500M000albumMID_1.jpg"] = (200, {"Content-Type": "text/html"})
    routes[stem + "R1200x1200M000albumMID_1.jpg"] = (404, {})
    assert await QQMusic()._best_cover(QQ_COVER) == stem + "R800x800M000albumMID_1.jpg"


async def test_qq_already_larger_cover_is_never_replaced_by_800px(music_http):
    routes, calls = music_http
    stem = "https://y.gtimg.cn/music/photo_new/T002"
    routes[stem + "M000albumMID_1.jpg"] = (404, {})
    cover = stem + "R2000x2000M000albumMID_1.jpg"
    assert await QQMusic()._best_cover(cover) == cover
    assert len(calls) == 1


@pytest.mark.parametrize("source", ["netease", "qqmusic"])
async def test_short_links_resolve_before_resource_detection(music_http, source):
    routes, _ = music_http
    if source == "netease":
        short, target = "https://163cn.tv/abc", "https://music.163.com/playlist?id=42"
        routes[target] = "landing page"
        routes["https://music.163.com/api/v6/playlist/detail?id=42&n=0"] = {
            "code": 200,
            "playlist": {"name": "封面", "coverImgUrl": NET_COVER},
        }
    else:
        short, target = "https://c6.y.qq.com/base/fcgi-bin/u?__=abc", "https://y.qq.com/n/ryqq_v2/playlist/42"
        routes[target] = '<meta property="og:image" content="https://qpic.y.qq.com/music_cover/hash/600">'
    routes[short] = (302, {"Location": target})
    info = await parser.parse_video_share_url(short)
    assert info.source == source and len(info.images) == 1


async def test_short_link_stops_at_the_app_share_page_without_fetching_it(music_http):
    routes, calls = music_http
    # App 分享页没登记在 routes 里：去抓它就会触发「意外的外网请求」
    routes["https://c6.y.qq.com/base/fcgi-bin/u?__=abc"] = (
        302,
        {"Location": "https://i.y.qq.com/v8/playsong.html?ADTAG=cbshare&songmid=songMID&type=0"},
    )
    routes["https://y.qq.com/n/ryqq_v2/songDetail/songMID"] = f'<meta property="og:image" content="{QQ_COVER}">'
    routes["https://y.gtimg.cn/music/photo_new/T002M000albumMID_1.jpg"] = (200, {"Content-Type": "image/jpeg"})
    info = await QQMusic().parse_share_url("https://c6.y.qq.com/base/fcgi-bin/u?__=abc")
    assert info.page_url == "https://y.qq.com/n/ryqq_v2/songDetail/songMID"
    assert [str(call.url) for call in calls][:2] == [
        "https://c6.y.qq.com/base/fcgi-bin/u?__=abc",
        "https://y.qq.com/n/ryqq_v2/songDetail/songMID",
    ]


async def test_short_link_follows_intermediate_hops_until_a_work_is_recognized(music_http):
    routes, _ = music_http
    routes["https://163cn.tv/abc"] = (302, {"Location": "https://163cn.tv/go?to=abc"})
    routes["https://163cn.tv/go?to=abc"] = (302, {"Location": "/landing?from=share"})
    routes["https://163cn.tv/landing?from=share"] = (302, {"Location": "https://y.music.163.com/m/song?id=186016"})
    routes["https://music.163.com/api/song/detail?ids=%5B186016%5D"] = {
        "code": 200,
        "songs": [{"name": "歌", "album": {"picUrl": NET_COVER}, "artists": [{"name": "歌手"}]}],
    }
    info = await NetEaseMusic().parse_share_url("https://163cn.tv/abc")
    assert info.page_url == "https://music.163.com/song?id=186016"


async def test_short_link_to_an_unsupported_page_says_so(music_http):
    routes, _ = music_http
    mv = "https://i2.y.qq.com/n3/other/pages/details/mv.html?vid=v00"
    routes["https://c6.y.qq.com/base/fcgi-bin/u?__=mv"] = (302, {"Location": mv})
    routes[mv] = "mv page"
    with pytest.raises(ParseError) as error:
        await QQMusic().parse_share_url("https://c6.y.qq.com/base/fcgi-bin/u?__=mv")
    assert error.value.reason == "unsupported"


def test_music_metadata_and_images_use_domestic_proxy_and_cdn_referers(monkeypatch):
    monkeypatch.setenv("PARSE_VIDEO_PROXY_CN", "http://cn:8080")
    for source, url, referer in (
        ("netease", NET_COVER, "https://music.163.com/"),
        ("qqmusic", "https://qpic.y.qq.com/music_cover/hash/1000", "https://y.qq.com/"),
        ("qqmusic", "https://y.gtimg.cn/music/photo_new/album.jpg", "https://y.qq.com/"),
    ):
        assert utils.proxy_for(source) == "http://cn:8080"
        assert utils.is_cn_url(url)
        assert net.headers_for(url)["Referer"] == referer
