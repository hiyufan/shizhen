"""公益备用仅补全身份一致的官方公开作品；不接凭证，不信任画质宣传标签。"""

import json

import httpx
import pytest

from qiuyuantu import utils
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
        if request.url.host == "channels.weixin.qq.com":
            reply = OFFICIAL
        else:
            reply = replies.get(request.url.host, replies.get("public"))
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


async def test_public_resolver_is_called_directly_not_through_domestic_relay(public_http, monkeypatch):
    replies, _ = public_http
    replies["public"] = public_result()
    sources = []
    real = base.create_async_client

    def recording(**kw):
        sources.append(utils.current_source.get())
        return real(**kw)

    monkeypatch.setattr(base, "create_async_client", recording)
    utils.current_source.set("wechat-channels")
    info = await wechat_channels.WeChatChannels().parse_share_url(SHARE)
    assert info.video_url == VIDEO
    # 官方预览走视频号自己的线路；公益接口直连，回来后来源恢复
    assert sources == ["wechat-channels", ""] and utils.current_source.get() == "wechat-channels"


async def test_public_resolver_never_sends_account_credentials_or_trusts_quality(public_http):
    replies, calls = public_http
    replies["public"] = public_result()
    info = await wechat_channels.WeChatChannels().parse_share_url(SHARE)
    assert info.video_url == VIDEO and info.formats[0].codec == "H.265"
    assert info.images[0].url == OFFICIAL["data"]["feedInfo"]["coverUrl"]
    assert info.height == 0 and info.width == 0 and info.formats[0].height == 0
    assert "公开解析服务" in info.notice
    assert dict(calls[1].url.params) == {"url": SHARE}
    # 默认用内置公益服务；没配 PARSE_VIDEO_CHANNELS_RESOLVERS 时只它一个
    assert calls[1].url.host == "api.bugpk.com"
    assert all("authorization" not in r.headers and "cookie" not in r.headers for r in calls)


async def test_tries_next_public_resolver_when_the_first_misses(public_http, monkeypatch):
    replies, calls = public_http
    monkeypatch.setenv("PARSE_VIDEO_CHANNELS_RESOLVERS", "https://first.example/x,https://second.example/y")
    mismatched = public_result()
    mismatched["data"]["title"] = "Different post"
    replies["first.example"] = mismatched
    replies["second.example"] = public_result()
    info = await wechat_channels.WeChatChannels().parse_share_url(SHARE)
    assert info.video_url == VIDEO
    # 官方预览 → 第一个服务（身份对不上）→ 第二个服务（采用）
    assert [c.url.host for c in calls] == ["channels.weixin.qq.com", "first.example", "second.example"]


async def test_non_https_resolver_endpoints_are_ignored(public_http, monkeypatch):
    replies, calls = public_http
    monkeypatch.setenv("PARSE_VIDEO_CHANNELS_RESOLVERS", "http://insecure.example/x,https://ok.example/y")
    replies["ok.example"] = public_result()
    info = await wechat_channels.WeChatChannels().parse_share_url(SHARE)
    assert info.video_url == VIDEO
    assert [c.url.host for c in calls] == ["channels.weixin.qq.com", "ok.example"]


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
