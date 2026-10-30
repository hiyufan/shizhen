"""闲鱼匿名详情回放：短链、原始素材、签名握手与失败分类。"""

import hashlib
import json

import httpx
import pytest

from qiuyuantu import parser, utils
from qiuyuantu.convert import net
from qiuyuantu.parser import base
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.goofish import APP_KEY, ENDPOINT, Goofish, item_id

SHORT = "https://m.tb.cn/h.j4xpcXL"
PAGE = "https://h5.m.goofish.com/item?itemId=977343683777&id=977343683777&shareurl=true"
IMAGE = "https://img.alicdn.com/bao/uploaded/photo.heic?keep=1"
VIDEO = "https://cloud.video.taobao.com/play/video.mp4?auth=keep"
IDENT = "977343683777"


def detail(**overrides):
    item = {"itemId": IDENT, "title": "商品图片", "imageInfos": [{"url": IMAGE.replace("https:", "http:")}]}
    return {"ret": ["SUCCESS::调用成功"], "data": {"itemDO": item | overrides, "sellerDO": {"nick": "公开昵称"}}}


@pytest.fixture
def goofish_http(monkeypatch):
    replies, calls = [], []

    def handle(request):
        calls.append(request)
        assert replies, f"意外的请求：{request.url}"
        reply = replies.pop(0)
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(base, "create_async_client", client)
    return replies, calls


@pytest.mark.parametrize(
    "url",
    [PAGE, f"https://www.goofish.com/item?id={IDENT}", f"https://2.taobao.com/item.htm?id={IDENT}"],
)
def test_supported_item_links_and_network_routing(url):
    assert item_id(url) == IDENT
    assert parser.detect_source(url) == VideoSource.Goofish
    assert "goofish" in utils.CN_SOURCES
    assert utils.is_cn_url(IMAGE) and utils.is_cn_url(VIDEO)
    assert net.referer_for(IMAGE) == net.referer_for(VIDEO) == "https://h5.m.goofish.com/"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.goofish.com/personal?userId=123",
        "https://www.goofish.com/item",
        "https://www.goofish.com/item?id=1%26other%3D2",
        "https://www.goofish.com/item?id=1&itemId=2",
        "https://www.goofish.com.evil.test/item?id=1",
        "https://item.taobao.com/item.htm?id=1",
        "ftp://www.goofish.com/item?id=1",
    ],
)
def test_invalid_and_other_taobao_links_are_unsupported(url):
    with pytest.raises(ParseError) as exc:
        item_id(url)
    assert exc.value.reason == "unsupported"


async def test_short_link_handshake_keeps_originals_and_cookie_isolation(goofish_http):
    replies, calls = goofish_http
    replies.extend(
        [
            httpx.Response(200, text=f"<script>var url = '{PAGE.replace('&', '&amp;')}';</script>"),
            httpx.Response(
                200,
                json={"ret": ["FAIL_SYS_TOKEN_EMPTY::令牌为空"]},
                headers=[
                    ("Set-Cookie", "_m_h5_tk=anon_123; Path=/; Secure"),
                    ("Set-Cookie", "_m_h5_tk_enc=encoded; Path=/; Secure"),
                    ("Set-Cookie", "cookie2=do-not-forward; Path=/; Secure"),
                ],
            ),
            detail(imageInfos=[{"url": IMAGE}, {"url": IMAGE}, {"url": IMAGE + "&second=1"}]),
            detail(),
        ]
    )
    info = await parser.parse_video_share_url(SHORT)
    assert info.source == "goofish" and info.page_url == f"https://www.goofish.com/item?id={IDENT}"
    assert info.title == "商品图片" and info.author.name == "公开昵称"
    assert [image.url for image in info.images] == [IMAGE, IMAGE + "&second=1"]
    assert info.cover_url == IMAGE and not info.video_url and not info.formats
    first, signed = calls[1:3]
    assert str(first.url).startswith(ENDPOINT)
    assert "cookie" not in first.headers and signed.headers["cookie"] == "_m_h5_tk=anon_123; _m_h5_tk_enc=encoded"
    params = signed.url.params
    payload = params["data"]
    assert json.loads(payload) == {"itemId": IDENT}
    assert params["sign"] == hashlib.md5(f"anon&{params['t']}&{APP_KEY}&{payload}".encode()).hexdigest()
    assert "Cookie" not in info.video_headers
    await Goofish().parse_video_id(IDENT)
    assert "cookie" not in calls[-1].headers
    assert utils.current_source.get() == ""


async def test_short_link_http_redirect_and_real_video_urls(goofish_http):
    replies, calls = goofish_http
    replies.extend(
        [
            httpx.Response(302, headers={"Location": PAGE}),
            httpx.Response(200, text="<div id='root'></div>"),
            detail(videoPlayInfo={"playUrl": VIDEO, "ld320pUrl": VIDEO, "url": IMAGE}),
        ]
    )
    info = await Goofish().parse_share_url(SHORT)
    assert info.video_url == VIDEO and info.cover_url == IMAGE
    assert [fmt.url for fmt in info.formats] == [VIDEO]
    assert not info.formats[0].format_spec
    assert len(calls) == 3


@pytest.mark.parametrize("page", ["<title>已失效</title>", "var url = 'https://item.taobao.com/item.htm?id=1';"])
async def test_short_link_never_interprets_unrelated_taobao_item(goofish_http, page):
    replies, calls = goofish_http
    replies.append(httpx.Response(200, text=page))
    with pytest.raises(ParseError) as exc:
        await Goofish().parse_share_url(SHORT)
    assert exc.value.reason == "unsupported" and len(calls) == 1


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ({"ret": ["FAIL_SYS_SESSION_EXPIRED::需要登录"]}, "login"),
        ({"ret": ["FAIL_BIZ_ITEM_NOT_EXIST::商品不存在"]}, "deleted"),
        ({"ret": ["FAIL_SYS_USER_VALIDATE::验证 https://secret.test/?token=hidden"]}, "blocked"),
        ({"ret": ["FAIL_SYS_TOKEN_EMPTY::令牌为空"]}, "blocked"),
        ({"ret": ["FAIL_OTHER::未知错误"]}, "parse"),
        (detail(itemId="another-item"), "parse"),
        (detail(imageInfos=[]), "empty"),
        (detail(imageInfos=[{"url": "https://127.0.0.1/private.jpg"}]), "parse"),
        (detail(videoPlayInfo={"ld320pUrl": "https://alicdn.com.evil.test/video.mp4"}), "parse"),
    ],
)
async def test_failure_categories_do_not_leak_response_details(goofish_http, reply, reason):
    replies, calls = goofish_http
    replies.append(reply)
    with pytest.raises(ParseError) as exc:
        await Goofish().parse_video_id(IDENT)
    assert exc.value.reason == reason
    assert "hidden" not in str(exc.value) and len(calls) == 1


async def test_token_refresh_is_bounded(goofish_http):
    replies, calls = goofish_http
    replies.extend(
        httpx.Response(
            200,
            json={"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"]},
            headers={"Set-Cookie": f"_m_h5_tk=anon_{i}; Path=/; Secure"},
        )
        for i in range(2)
    )
    with pytest.raises(ParseError) as exc:
        await Goofish().parse_video_id(IDENT)
    assert exc.value.reason == "blocked" and len(calls) == 2
