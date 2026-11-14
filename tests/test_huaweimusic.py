"""回放华为音乐匿名封面接口；不读取歌曲音频、账号凭证或真实数据目录。"""

import copy
import json

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.convert import animated, ffmpeg, jobs, store, tasks
from qiuyuantu.parser import base, huaweimusic
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.errors import ParseError

STATIC = "https://contentcenter-drcn.dbankcdn.cn/img/pub_1/HW-Music_cover_100_1/ab/v3/hash/cover.jpg/music_cover_1000x1000.webp"
DYNAMIC = "https://contentcenter-drcn.dbankcdn.cn/img/pub_1/HW-Music_DynamicCover_100_1/df/v3/hash/cover.webp/music_dynamiccover_1000x1000.webp"
SHORT = "https://url.cloud.huawei.com/example"
SONG = {
    "contentID": "2168177",
    "contentName": "大鱼",
    "contentType": "1",
    "albumID": "168016",
    "artistName": "周深",
    "picture": {"bigImgURL": STATIC, "dynamicImgURL": DYNAMIC},
    "contentExInfo": json.dumps({"fileInfos": [{"previewURL": "https://audio.invalid/music.mkv"}]}),
}


@pytest.fixture
def music_http(monkeypatch):
    calls, replies = [], {}

    def handle(request):
        calls.append(request)
        assert request.url.host in {huaweimusic.HOST, huaweimusic.SHORT_HOST}
        assert "cookie" not in request.headers and "authorization" not in request.headers
        assert request.url.path in replies, f"意外请求：{request.url}"
        reply = replies[request.url.path]
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    return calls, replies


def song_reply(replies, song=None):
    replies["/music-metacontent-service/v1/service/song/detail/bysongcode"] = {
        "result": {"resultCode": "000000"},
        "songInfo": copy.deepcopy(song or SONG),
    }


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("1?id=2168177&shareChannel=copyLink", ("song", "2168177", 0)),
        ("1?id=M_b-4EQuyWFISLIOX", ("song", "M_b-4EQuyWFISLIOX", 0)),
        ("2?id=168016", ("album", "168016", 0)),
        ("4?id=OqsyPlZ9qWO0pHDHE&start=20", ("playlist", "OqsyPlZ9qWO0pHDHE", 20)),
        ("5?id=unused&listid=u.opaque_1-2", ("playlist", "u.opaque_1-2", 0)),
    ],
)
def test_share_routes_and_opaque_ids(route, expected):
    assert huaweimusic.target(huaweimusic.PAGE + "#/musicshare/" + route) == expected


@pytest.mark.parametrize(
    "url",
    [
        huaweimusic.PAGE.replace("https:", "http:") + "#/musicshare/1?id=1",
        huaweimusic.PAGE.replace(huaweimusic.HOST, huaweimusic.HOST + ".evil.test") + "#/musicshare/1?id=1",
        huaweimusic.PAGE.replace(huaweimusic.HOST, "user@" + huaweimusic.HOST) + "#/musicshare/1?id=1",
        huaweimusic.PAGE + "#/musicshare/3?id=1",
        huaweimusic.PAGE + "#/musicshare/23?id=1",
        huaweimusic.PAGE + "#/musicshare/1?id=../../x",
        huaweimusic.PAGE + "#/musicshare/1?id=1%26other%3D2",
        huaweimusic.PAGE + "#/musicshare/1?id=1&start=20",
        huaweimusic.PAGE + "#/musicshare/4?id=1&start=10001",
        huaweimusic.PAGE + "#/musicshare/4?id=1&start=-1",
        huaweimusic.PAGE + "#/musicshare/1",
        huaweimusic.PAGE + "#/musicshare/1?id=" + "a" * 65,
    ],
)
def test_unrelated_or_invalid_pages_are_rejected(url):
    with pytest.raises(ParseError, match="华为音乐"):
        huaweimusic.target(url)


async def test_short_share_returns_original_webp_and_separate_static_cover(music_http):
    calls, replies = music_http
    replies["/example"] = lambda _: httpx.Response(302, headers={"Location": huaweimusic.page_url("song", "2168177")})
    song_reply(replies)
    info = await parser.parse_video_share_url(SHORT)
    assert parser.detect_source(SHORT) is VideoSource.HuaweiMusic
    assert info.source == "huaweimusic" and info.title == "大鱼" and info.author.name == "周深"
    assert info.cover_url == info.images[0].url == STATIC
    assert len(info.assets) == 1 and info.assets[0].url == DYNAMIC
    assert (info.assets[0].kind, info.assets[0].ext) == ("animation", "webp")
    assert not info.video_url and not info.music_url and not info.formats
    assert info.page_url == huaweimusic.page_url("song", "2168177")
    assert len(calls) == 2 and calls[-1].url.params["songCode"] == "2168177"


async def test_unrelated_cloud_short_url_does_not_follow_off_platform_redirect(music_http):
    calls, replies = music_http
    replies["/example"] = lambda _: httpx.Response(302, headers={"Location": "https://outside.invalid/anything"})
    with pytest.raises(ParseError, match="分享链接"):
        await huaweimusic.HuaweiMusic().parse_share_url(SHORT)
    assert len(calls) == 1


async def test_cloud_short_link_loops_are_bounded(music_http):
    calls, replies = music_http
    replies["/example"] = lambda _: httpx.Response(302, headers={"Location": SHORT})
    with pytest.raises(ParseError):
        await huaweimusic.HuaweiMusic().parse_share_url(SHORT)
    assert len(calls) == 4


@pytest.mark.parametrize(
    "dynamic",
    [
        "",
        "https://evil.test/cover.webp",
        STATIC,
        DYNAMIC.replace(".cn/", ".cn.evil.test/"),
        DYNAMIC.replace("https:", "http:"),
        None,
    ],
)
async def test_static_cover_survives_missing_or_untrusted_animation(music_http, dynamic):
    _, replies = music_http
    song = copy.deepcopy(SONG)
    song["picture"]["dynamicImgURL"] = dynamic
    song_reply(replies, song)
    info = await huaweimusic.HuaweiMusic().parse_video_id("2168177")
    assert info.images[0].url == STATIC and not info.assets
    assert "未公开动态封面" in info.notice


@pytest.mark.parametrize("mismatch", ["none", "album", "picture", "recommended"])
async def test_album_uses_only_its_own_matching_cover(music_http, mismatch):
    calls, replies = music_http
    song = copy.deepcopy(SONG)
    if mismatch == "album":
        song["albumID"] = "other"
    if mismatch == "picture":
        song["picture"]["bigImgURL"] = STATIC.replace("cover.jpg", "other.jpg")
    songs = [] if mismatch == "recommended" else [song]
    replies["/music-odpapp-service/v2/service/album/detail/byalbumcode"] = {
        "result": {"resultCode": "000000"},
        "albumInfoEx": {
            "albumInfo": {"contentID": "168016", "contentName": "大鱼", "picture": {"bigImgURL": STATIC}},
            "songSimpleInfos": songs,
        },
        "recommended": [song],
    }
    info = await huaweimusic.HuaweiMusic().parse_video_id("album:168016")
    assert bool(info.assets) == (mismatch == "none")
    assert len(calls) == 1 and calls[0].url.params["limit"] == "20"


async def test_playlist_is_a_paged_directory_not_bulk_media(music_http):
    calls, replies = music_http
    songs = [dict(SONG, contentID=str(i)) for i in range(20)]
    replies["/music-operation-service/v2/service/musiclist/detail/bymusiclistid"] = {
        "result": {"resultCode": "000000"},
        "musicListInfoEx": {
            "musicListDetail": {
                "contentID": "opaque_1",
                "contentName": "动态封面",
                "totalCount": "21",
                "picture": {"bigImgURL": STATIC},
            },
            "songSimpleInfos": songs,
        },
    }
    info = await parser.parse_video_share_url(huaweimusic.page_url("playlist", "opaque_1"))
    assert info.content_kind == "directory" and info.entry_total == 21 and len(info.entries) == 20
    assert not info.images and not info.assets and not info.formats and not info.video_url
    assert info.entries[0].url == huaweimusic.page_url("song", "0") and info.entries[0].cover_url == STATIC
    assert info.next_page_url == huaweimusic.page_url("playlist", "opaque_1", 20)
    assert json.loads(calls[0].content) == {"musicListID": "opaque_1", "start": 0, "limit": 20}
    await parser.parse_video_share_url(info.next_page_url)
    assert json.loads(calls[-1].content)["start"] == 20


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ({"result": {"resultCode": "100001"}}, "restricted"),
        ({"result": {"resultCode": "000000"}, "songInfo": dict(SONG, contentID="other")}, "empty"),
    ],
)
async def test_rejected_or_mismatched_responses_are_not_success(music_http, reply, reason):
    _, replies = music_http
    replies["/music-metacontent-service/v1/service/song/detail/bysongcode"] = reply
    with pytest.raises(ParseError) as caught:
        await huaweimusic.HuaweiMusic().parse_video_id("2168177")
    assert caught.value.reason == reason


@pytest.mark.parametrize(("url", "expected"), [(DYNAMIC, 200_000_000), (STATIC, 100_000_000)])
async def test_native_cover_pixel_budget_does_not_change_other_images(tmp_path, monkeypatch, url, expected):
    monkeypatch.setattr(tasks.config, "SOURCES_DIR", tmp_path)

    async def download(_url, path, _headers, _progress):
        path.write_bytes(b"image")

    async def prepare(source, destination, *, alpha_destination, max_frame_pixels=100_000_000):
        assert max_frame_pixels == expected and source.read_bytes() == b"image"
        destination.write_bytes(b"video")
        alpha_destination.write_bytes(b"alpha")

    async def probe(_path):
        return ffmpeg.ProbeInfo(duration=9.6, width=1000, height=1000, fps=1000 / 60)

    monkeypatch.setattr(tasks, "download_source", download)
    monkeypatch.setattr(animated, "prepare", prepare)
    monkeypatch.setattr(ffmpeg, "probe", probe)
    result = await tasks.fetch_source(
        jobs.Job(id="huawei", type="prepare"), source_id=store.source_id_for(url), url=url, image=True
    )
    assert result.gif_path and result.width == 1000
    assert not list(tmp_path.glob("*.image"))
