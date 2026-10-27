"""美拍现代 PHPDATA 与旧混淆链接均保留，媒体身份必须与请求相符。"""

import base64
import json

import httpx
import pytest

from qiuyuantu.parser import base, mp4_probe
from qiuyuantu.parser.errors import ParseError
from qiuyuantu.parser.meipai import MeiPai, decode_video_url


def _encoded(url):
    raw = base64.b64encode(url.encode()).decode()
    return "2d40" + raw[:1] + "XX" + raw[1:-3] + "YYYY" + raw[-3:]


@pytest.fixture
def network(monkeypatch):
    routes = {}

    def response(request):
        return httpx.Response(200, text=routes[str(request.url)])

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(response), **kw)
    )

    async def probe(*_args):
        return None

    monkeypatch.setattr(mp4_probe, "probe", probe)
    return routes


@pytest.mark.parametrize("url", ["//cdn.test/video.mp4", "https://cdn.test/video.mp4"])
def test_decoder_accepts_protocol_relative_and_absolute_addresses_without_duplicating_scheme(url):
    assert decode_video_url(_encoded(url)) == "https://cdn.test/video.mp4"


async def test_modern_route_reloads_canonical_media_page_instead_of_decoding_an_empty_string(network):
    network["https://www.meipai.com/video/533/123"] = "<html>mobile shell</html>"
    media = {
        "id": "123",
        "caption_origin": "<p>caption };</p>",
        "video": _encoded("//cdn.test/720.mp4"),
        "cover_pic": "//cdn.test/cover.jpg",
        "user": {"id": 1, "screen_name": "author"},
    }
    network["https://www.meipai.com/media/123"] = "window.PHPDATA = " + json.dumps({"mediaInfo": media}) + ";other();"
    info = await MeiPai().parse_share_url("http://www.meipai.com/video/533/123")
    assert info.video_url == "https://cdn.test/720.mp4" and info.title == "caption };"
    assert info.author.name == "author" and info.author.avatar == ""
    assert info.page_url == "https://www.meipai.com/media/123"


async def test_existing_legacy_page_remains_supported(network):
    encoded = _encoded("//cdn.test/legacy.mp4")
    network["https://www.meipai.com/video/123"] = f'<a id="shareMediaBtn" data-video="{encoded}"></a>'
    assert (await MeiPai().parse_video_id("123")).video_url == "https://cdn.test/legacy.mp4"


async def test_php_data_for_a_different_media_is_not_returned_as_the_requested_work(network):
    network["https://www.meipai.com/media/123"] = (
        "window.PHPDATA=" + json.dumps({"mediaInfo": {"id": "456", "video": _encoded("//cdn.test/other.mp4")}}) + ";"
    )
    with pytest.raises(ParseError, match="作品编号"):
        await MeiPai().parse_share_url("https://www.meipai.com/media/123")
