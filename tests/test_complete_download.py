"""Range 下载必须覆盖整个对象，不能把 CDN 截短的分轨登记成成功。"""

import httpx
import pytest

from qiuyuantu.convert.fetch import _stream_to_file


@pytest.mark.parametrize("content_range", ["bytes 0-3/8", "bytes 4-7/8", "bytes 0-3/*", "", "bytes 0-3/0"])
async def test_incomplete_range_is_rejected_before_writing(tmp_path, content_range):
    path = tmp_path / "track.m4s"
    transport = httpx.MockTransport(
        lambda r: httpx.Response(206, content=b"abcd", headers={"Content-Range": content_range})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="部分文件"):
            await _stream_to_file(
                client, "https://example.com/track", path, headers={"Range": "bytes=0-"}, limit=10, too_large="large"
            )
    assert not path.exists()


@pytest.mark.parametrize("status", [200, 206])
async def test_complete_range_or_normal_response_preserves_all_bytes(tmp_path, status):
    path = tmp_path / "track.m4s"
    headers = {"Content-Range": "bytes 0-3/4"} if status == 206 else {}
    transport = httpx.MockTransport(lambda r: httpx.Response(status, content=b"abcd", headers=headers))
    async with httpx.AsyncClient(transport=transport) as client:
        await _stream_to_file(
            client, "https://example.com/track", path, headers={"Range": "bytes=0-"}, limit=10, too_large="large"
        )
    assert path.read_bytes() == b"abcd"
