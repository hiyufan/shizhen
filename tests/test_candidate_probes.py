"""研究原型只汇总响应结构，不能把预览元数据当作完整下载能力。"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location(
    "probe_media_candidates", Path(__file__).resolve().parents[1] / "scripts/probe_media_candidates.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_channels_parse_stage_never_prints_credential_or_signed_url():
    result = probe.channels_response(
        {
            "data": {
                "wx_export_id": "secret-export",
                "playable_url": "https://channels.weixin.qq.com/finder-preview/pages/feed?eid=secret-export&token=secret-token",
            }
        }
    )
    assert result["stage"] == "feed_lookup_required"
    assert result["has_export_id"] and result["has_general_token"]
    assert "secret-" not in json.dumps(result) and not result["download_verified"]


def test_channels_feed_and_encryption_are_only_metadata_evidence():
    result = probe.channels_response(
        {
            "data": {
                "data": {
                    "feedInfo": {
                        "videoUrl": "https://example.com/a.mp4?token=secret",
                        "h264VideoInfo": {"videoUrl": "https://example.com/a.mp4?token=secret"},
                        "picInfo": [{"url": "https://example.com/a.jpg"}],
                    }
                }
            }
        }
    )
    assert result["video_variants"] == 1 and result["pictures"] == 1
    assert not result["download_verified"] and "secret" not in json.dumps(result)
    result = probe.channels_response({"objectDesc": {"media": [{"url": "x", "decodeKey": "123456789"}]}})
    assert result["encrypted_media"] == 1 and "123456789" not in json.dumps(result)
    assert probe.channels_response({"errCode": -1})["stage"] == "unrecognized_or_restricted"


def pixiv_data():
    return (
        {"error": False, "body": {"id": "151302250", "illustType": 2, "pageCount": 1, "xRestrict": 0}},
        {
            "error": False,
            "body": {
                "src": "https://i.pximg.net/img-zip-ugoira/img/151302250_ugoira600x600.zip?token=private",
                "originalSrc": "https://i.pximg.net/img-zip-ugoira/img/151302250_ugoira1920x1080.zip",
                "frames": [{"file": "000000.jpg", "delay": 120}, {"file": "000001.jpg", "delay": 130}],
            },
        },
    )


def test_pixiv_reports_actual_frame_delays_without_claiming_download_or_dimensions():
    info, animation = pixiv_data()
    result = probe.pixiv_response("151302250", info, animation)
    assert result["frames"] == 2 and result["duration_ms"] == 250 and result["variable_frame_timing"]
    assert result["has_preview_zip"] and result["has_larger_zip"]
    assert not result["download_verified"] and not result["actual_frame_dimensions_verified"]
    assert "private" not in json.dumps(result) and "pximg" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["identity", "traversal", "timing", "error"])
def test_pixiv_rejects_unverified_identity_and_invalid_frame_metadata(failure):
    info, animation = pixiv_data()
    if failure == "identity":
        info["body"]["id"] = "999999999"
    elif failure == "traversal":
        animation["body"]["frames"][0]["file"] = "../000000.jpg"
    elif failure == "timing":
        animation["body"]["frames"][0]["delay"] = -1
    else:
        animation["error"] = True
    with pytest.raises(ValueError):
        probe.pixiv_response("151302250", info, animation)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/artworks/151302250",
        "https://www.pixiv.net.evil.example/artworks/151302250",
        "https://user:password@www.pixiv.net/artworks/151302250",
        "https://www.pixiv.net/users/151302250",
    ],
)
def test_pixiv_probe_only_accepts_official_artwork_links(url):
    with pytest.raises(ValueError):
        probe.pixiv_id(url)


async def test_pixiv_probe_uses_only_official_anonymous_web_requests(monkeypatch):
    from qiuyuantu import utils

    info, animation = pixiv_data()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=animation if request.url.path.endswith("/ugoira_meta") else info)

    monkeypatch.setattr(
        utils, "create_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    )
    result = await probe.pixiv_preview("https://www.pixiv.net/en/artworks/151302250")
    assert result["variable_frame_timing"] and len(calls) == 2
    assert all(request.url.host == "www.pixiv.net" for request in calls)
    assert all("cookie" not in request.headers and "authorization" not in request.headers for request in calls)
