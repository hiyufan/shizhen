"""绿洲图集只取正文图片，较大的图片地址必须先验证。"""

import httpx

from qiuyuantu.parser import base, lvzhou

PAGE = "https://oasis.weibo.cn/v1/h5/share?sid=123"
PREVIEW = "https://lz.sinaimg.cn/nmw690/photo.jpg"


async def test_image_post_keeps_all_body_images_and_uses_verified_originals(monkeypatch):
    html = (
        '<a class="avatar"><img src="https://image.test/avatar.jpg"></a><div class="media">'
        f'<img src="{PREVIEW}"><img src="{PREVIEW.replace("photo", "second")}"></div>'
        '<div class="status-text">gallery</div>'
    )
    monkeypatch.setattr(
        base,
        "create_async_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html)), **kwargs
        ),
    )

    async def probe(url):
        return (690, 690) if "/nmw690/" in url else (1280, 1280)

    monkeypatch.setattr(lvzhou.image_probe, "probe", probe)
    info = await lvzhou.LvZhou().parse_share_url(PAGE)
    assert not info.video_url and info.title == "gallery"
    assert [image.url for image in info.images] == [
        PREVIEW.replace("nmw690", "original"),
        PREVIEW.replace("nmw690", "original").replace("photo", "second"),
    ]


async def test_invalid_or_smaller_placeholders_never_replace_a_working_preview(monkeypatch):
    async def probe(url):
        return (690, 690) if "/nmw690/" in url else (180, 180)

    monkeypatch.setattr(lvzhou.image_probe, "probe", probe)
    assert await lvzhou._original_image(PREVIEW) == PREVIEW


async def test_full_panorama_is_not_replaced_by_a_square_crop_with_more_pixels(monkeypatch):
    async def probe(url):
        return (1280, 64) if "/original/" in url else (690, 690)

    monkeypatch.setattr(lvzhou.image_probe, "probe", probe)
    assert await lvzhou._original_image(PREVIEW) == PREVIEW.replace("nmw690", "original")
