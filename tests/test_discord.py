"""匿名目录匹配、原始动画格式及消息链接的边界。"""

import copy

import httpx
import pytest

from qiuyuantu import parser
from qiuyuantu.parser import base
from qiuyuantu.parser.base import VideoSource
from qiuyuantu.parser.discord import CATALOG, CDN, Discord
from qiuyuantu.parser.errors import ParseError

PACK = "847199849233514549"
SKU = "847199849233514547"
STICKER = "1228092333061443654"
LOTTIE = "796140620111544330"
DATA = {
    "sticker_packs": [
        {
            "id": PACK,
            "sku_id": SKU,
            "name": "Public pack",
            "stickers": [
                {"id": STICKER, "name": "APNG sticker", "format_type": 2},
                {"id": LOTTIE, "name": "Lottie sticker", "format_type": 3},
            ],
        }
    ]
}


@pytest.fixture
def catalog(monkeypatch):
    state = {"response": copy.deepcopy(DATA), "calls": []}

    def handle(request):
        state["calls"].append(request)
        response = state["response"]
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    return state


@pytest.mark.parametrize(
    "url",
    [
        f"https://discord.com/sticker-packs/{PACK}",
        f"https://discord.com/api/v10/sticker-packs/{PACK}",
        f"https://discord.com/store/skus/{SKU}/public-pack",
    ],
)
async def test_public_pack_uses_only_anonymous_catalog_and_separate_assets(catalog, url):
    info = await parser.parse_video_share_url(url)
    assert info.source == "discord" and info.title == "Public pack"
    assert not info.video_url and not info.formats and not info.images
    assert info.assets[0].url == CDN + STICKER + ".png" and info.assets[0].kind == "animation"
    assert info.assets[1].url == CDN + LOTTIE + ".json" and not info.assets[1].cover_url
    assert info.page_url == "https://discord.com/store/skus/" + SKU
    assert len(catalog["calls"]) == 1
    request = catalog["calls"][0]
    assert str(request.url) == CATALOG
    assert "cookie" not in request.headers and "authorization" not in request.headers


async def test_single_sticker_is_matched_by_id_and_catalog_format(catalog):
    info = await parser.parse_video_share_url(f"https://media.discordapp.net/stickers/{LOTTIE}.png?size=320")
    assert info.title == "Lottie sticker" and len(info.assets) == 1
    assert info.assets[0].ext == "json" and info.assets[0].url == CDN + LOTTIE + ".json"
    assert not info.cover_url


@pytest.mark.parametrize("status", [401, 404, 429])
async def test_api_failure_never_tries_credentials_or_other_endpoints(catalog, status):
    catalog["response"] = httpx.Response(status)
    with pytest.raises(ParseError):
        await Discord().parse_video_id(STICKER)
    assert len(catalog["calls"]) == 1


@pytest.mark.parametrize("failure", ["missing", "wrong_pack", "empty", "unknown_format", "untrusted_id", "huge_pack"])
async def test_missing_or_invalid_catalog_cannot_become_success(catalog, failure):
    pack = catalog["response"]["sticker_packs"][0]
    if failure == "missing":
        catalog["response"] = {"message": "not a catalog"}
    elif failure == "wrong_pack":
        pack["id"] = "111111111111111111"
    elif failure == "empty":
        pack["stickers"] = []
    elif failure == "unknown_format":
        pack["stickers"] = [{"id": STICKER, "format_type": 999}]
    elif failure == "untrusted_id":
        pack["stickers"][0]["id"] = "../private"
    else:
        pack["stickers"] *= 101
    with pytest.raises(ParseError):
        await Discord().parse_share_url(f"https://discord.com/sticker-packs/{PACK}")


@pytest.mark.parametrize(
    "url",
    [
        f"https://discord.com/channels/{PACK}/{STICKER}",
        f"https://discord.com.evil.example/stickers/{STICKER}.png",
        f"https://cdn.discordapp.com/attachments/{PACK}/{STICKER}/image.png",
    ],
)
def test_message_and_other_media_links_keep_existing_routing(url):
    assert parser.detect_source(url) == VideoSource.YtDlp


async def test_non_catalog_sticker_never_silently_returns_another(catalog):
    with pytest.raises(ParseError, match="不在 Discord 当前公开目录"):
        await Discord().parse_video_id("111111111111111111")
