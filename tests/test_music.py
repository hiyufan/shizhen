"""音乐封面公开响应的离线回放；不请求歌曲、不依赖账号 Cookie。"""

import httpx
import pytest

from qiuyuantu import parser, utils
from qiuyuantu.convert import net
from qiuyuantu.parser import base
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.music import NetEaseMusic, QQMusic, netease_cover, netease_target, qq_cover, qq_target

NET_COVER = "https://p1.music.126.net/hash/123.jpg"
QQ_COVER = "https://y.qq.com/music/photo_new/T002R300x300M000albumMID_1.jpg?max_age=2592000"


@pytest.fixture
def music_http(monkeypatch):
    routes, calls = {}, []

    def handle(request):
        url = str(request.url)
        calls.append(request)
        assert url in routes, f"意外的外网请求：{url}"
        reply = routes[url]
        if isinstance(reply, dict):
            return httpx.Response(200, json=reply)
        if isinstance(reply, tuple):
            return httpx.Response(reply[0], headers=reply[1])
        return httpx.Response(200, text=reply)

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(base, "create_async_client", client)
    return routes, calls


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
    assert len(calls) == 1 and calls[0].headers["referer"] == "https://music.163.com/"
    assert not calls[0].headers.get("cookie")


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
    info = await parser.parse_video_share_url(f"https://y.qq.com/n/ryqq/{kind}/42")
    assert info.title == "歌单 & 封面" and info.author.name == "歌手"
    assert info.images[0].url == "https://y.gtimg.cn/music/photo_new/T002R800x800M000albumMID_1.jpg"
    assert info.cover_url == info.images[0].url and info.source == "qqmusic"
    assert not info.video_url and not info.music_url and not info.formats
    assert len(calls) == 1 and calls[0].headers["referer"] == "https://y.qq.com/"


async def test_qq_page_without_cover_does_not_return_site_logo(music_http):
    routes, _ = music_http
    routes["https://y.qq.com/n/ryqq_v2/playlist/42"] = '<meta property="og:title" content="QQ音乐">'
    with pytest.raises(ParseError) as exc:
        await QQMusic().parse_video_id("playlist:42")
    assert exc.value.reason == "empty"


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
