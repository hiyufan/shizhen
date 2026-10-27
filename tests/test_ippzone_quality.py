"""皮皮搞笑和最右的原始地址、多档列表都需要参与实际文件比较。"""

import json

import httpx

from qiuyuantu.parser import VideoSource, base, detect_source, mp4_probe
from qiuyuantu.parser.base import FormatInfo, VideoInfo
from qiuyuantu.parser.ippzone import first_video, post_images, video_formats
from qiuyuantu.parser.pipigaoxiao import PiPiGaoXiao
from qiuyuantu.parser.quality import apply_video_formats
from qiuyuantu.web.limits import looks_like_image


async def test_source_and_other_qualities_are_compared_by_actual_dimensions(monkeypatch):
    async def probe(url, _headers):
        height = 1080 if "source" in url else 720 if "quality" in url else 540
        return FormatInfo(url=url, width=height * 16 // 9, height=height, fps=30)

    monkeypatch.setattr(mp4_probe, "probe", probe)
    video = {
        "url": "https://cdn.test/default.mp4",
        "urlsrc": "https://cdn.test/source.mp4",
        "qualities": [{"resolution": "4K", "urls": [{"url": "https://cdn.test/quality.mp4"}]}],
    }
    info = apply_video_formats(VideoInfo("", ""), await video_formats(video, "https://h5.pipigx.com/"))
    assert info.video_url.endswith("source.mp4") and info.height == 1080
    assert sorted(fmt.height for fmt in info.formats) == [540, 720]


async def test_watermarked_url_is_not_used_as_a_higher_bitrate_original():
    formats = await video_formats({"url": "original.mp4", "urlwm": "watermarked.mp4"}, "https://h5.pipigx.com/")
    assert [fmt.url for fmt in formats] == ["original.mp4"]


def test_current_ippzone_share_host_routes_to_native_parser_without_claiming_media_cdn_hosts():
    assert detect_source("https://h5.ippzone.com/spacey/post/897800342766") == VideoSource.PiPiGaoXiao
    assert detect_source("https://file.ippzone.com/img/view/id/123") != VideoSource.PiPiGaoXiao


def _image(ident=1):
    return {
        "id": ident,
        "urls": {
            "540": {"w": 540, "h": 720, "urls": [f"https://file.ippzone.com/img/view/id/{ident}/sz/540"]},
            "origin": {"w": 1080, "h": 1441, "urls": [f"https://file.ippzone.com/img/view/id/{ident}/sz/src"]},
        },
    }


async def test_image_only_post_uses_original_images_without_requiring_videos_field(monkeypatch):
    post = {"id": 42, "content": "images", "imgs": [_image(1), _image(2)]}

    def response(request):
        assert json.loads(request.content)["pid"] == 42
        return httpx.Response(200, json={"data": {"post": post}})

    monkeypatch.setattr(
        base, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(response), **kw)
    )
    info = await PiPiGaoXiao().parse_share_url("https://h5.ippzone.com/spacey/post/42")
    assert not info.video_url and len(info.images) == 2
    assert all(image.url.endswith("/sz/src") for image in info.images)


def test_mixed_post_keeps_images_and_finds_video_after_first_picture():
    video = {"url": "video.mp4"}
    post = {"imgs": [_image(1), {"id": 2, "video": 1}], "videos": {"2": video}}
    assert first_video(post) == video
    assert len(post_images(post)) == 1


def test_extensionless_image_routes_use_image_concurrency_without_exempting_video_paths():
    assert looks_like_image("https://bd-file.ippzone.com/img/view/id/123/sz/src")
    assert not looks_like_image("https://bd-file.ippzone.com.evil.test/img/view/id/123/sz/src")
    assert not looks_like_image("https://file.ippzone.com/video/123")
