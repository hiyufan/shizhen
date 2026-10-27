"""公益备用仅补全身份一致的官方公开作品；不接凭证，不信任画质宣传标签。"""

import json

import httpx
import pytest

from qiuyuantu.parser import base, channels_public, wechat_channels

SHARE = "https://weixin.qq.com/sph/PublicLink"
VIDEO = "https://finder.video.qq.com/raw.mp4?token=public"
OFFICIAL = {
    "errCode": 0,
    "data": {
        "feedInfo": {"description": "Public post", "coverUrl": "https://finder.video.qq.com/cover.jpg"},
        "authorInfo": {"nickname": "Author"},
        "errMsg": {"type": 0},
    },
}


@pytest.fixture
def public_http(monkeypatch):
    replies, calls = {}, []
    monkeypatch.setenv("PARSE_VIDEO_CHANNELS_PUBLIC_FALLBACK", "1")
    monkeypatch.setattr(wechat_channels, "resolver", channels_public.PublicResolver())

    def handle(request):
        calls.append(request)
        reply = OFFICIAL if request.url.host == "channels.weixin.qq.com" else replies["public"]
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    return replies, calls


def public_result():
    return {
        "code": 200,
        "data": {
            "type": "video",
            "title": "Public post",
            "author": {"name": "Author"},
            "url": VIDEO,
            "quality": "1080p",
            "video_backup": [
                {"url": VIDEO, "format": "mp4", "codec": "h264", "quality": "1080p"},
                {"url": VIDEO + "&codec=hevc", "format": "mp4", "codec": "h265", "quality": "1080p"},
            ],
        },
    }


async def test_public_resolver_never_sends_account_credentials_or_trusts_quality(public_http):
    replies, calls = public_http
    replies["public"] = public_result()
    info = await wechat_channels.WeChatChannels().parse_share_url(SHARE)
    assert info.video_url == VIDEO and info.formats[0].codec == "H.265"
    assert info.images[0].url == OFFICIAL["data"]["feedInfo"]["coverUrl"]
    assert info.height == 0 and info.width == 0 and info.formats[0].height == 0
    assert "BugPk" in info.notice
    assert dict(calls[1].url.params) == {"url": SHARE}
    assert calls[1].url.host == "api.bugpk.com"
    assert all("authorization" not in r.headers and "cookie" not in r.headers for r in calls)


@pytest.mark.parametrize("change", ["title", "author", "bad_host", "error", "unstructured"])
async def test_mismatch_or_failed_provider_keeps_only_official_cover(public_http, change):
    replies, _ = public_http
    data = public_result()
    if change == "title":
        data["data"]["title"] = "Different post"
    elif change == "author":
        data["data"]["author"] = {"name": "Other"}
    elif change == "bad_host":
        data["data"]["url"] = "https://127.0.0.1/private"
    elif change == "error":
        data = httpx.Response(503)
    else:
        data["data"]["author"] = "not an author record"
    replies["public"] = data
    info = await wechat_channels.WeChatChannels().parse_share_url(SHARE)
    assert not info.video_url and "只提供封面" in info.notice
    assert info.images[0].url == OFFICIAL["data"]["feedInfo"]["coverUrl"]


async def test_disable_public_resolver_sends_only_official_request(public_http, monkeypatch):
    _, calls = public_http
    monkeypatch.setenv("PARSE_VIDEO_CHANNELS_PUBLIC_FALLBACK", "0")
    info = await wechat_channels.WeChatChannels().parse_share_url(SHARE)
    assert not info.video_url and len(calls) == 1
    assert json.loads(calls[0].content)["baseReq"]["generalToken"] == ""
