"""图片尺寸探测必须有界读取，并在成功、超限与取消后关闭网络流。"""

import asyncio
import io

import httpx
import pytest
from PIL import Image

from qiuyuantu.parser import image_probe


@pytest.fixture
def network(monkeypatch):
    def install(handler):
        monkeypatch.setattr(
            image_probe,
            "safe_client",
            lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **{k: v for k, v in kwargs.items() if k != "for_url"}
            ),
        )

    return install


def _png():
    output = io.BytesIO()
    Image.new("RGB", (1280, 720)).save(output, format="PNG")
    return output.getvalue()


async def test_actual_dimensions_do_not_require_loading_the_full_image(network):
    body = _png()

    def reply(request):
        assert request.headers["range"] == "bytes=0-262143"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, content=body)

    network(reply)
    assert await image_probe.probe("https://image.test/large.jpg") == (1280, 720)


async def test_ignored_range_does_not_download_the_whole_image(network):
    seen = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(100):
                seen.append(index)
                yield _png().ljust(16384, b"\0") if not index else bytes(16384)

        async def aclose(self):
            seen.append("closed")

    network(lambda _: httpx.Response(200, stream=Stream()))
    assert await image_probe.probe("https://image.test/large.png") == (1280, 720)
    assert seen == [0, "closed"]


async def test_invalid_image_cannot_exceed_the_header_read_limit(network):
    seen = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(100):
                seen.append(index)
                yield bytes(16384)

        async def aclose(self):
            seen.append("closed")

    network(lambda _: httpx.Response(200, stream=Stream()))
    assert await image_probe.probe("https://image.test/not-an-image") is None
    assert seen == [*range(16), "closed"]


async def test_cancelled_probe_closes_its_connection(network):
    reading, closed = asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            reading.set()
            await asyncio.Future()

        async def aclose(self):
            closed.set()

    network(lambda _: httpx.Response(200, stream=Stream()))
    task = asyncio.create_task(image_probe.probe("https://image.test/slow"))
    await reading.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.parametrize("content_range", ["", "bytes 1-100/1000", "bytes 0-1000/1000"])
async def test_incorrect_partial_response_cannot_claim_image_dimensions(network, content_range):
    network(lambda _: httpx.Response(206, headers={"Content-Range": content_range}, content=_png()))
    assert await image_probe.probe("https://image.test/bad-range") is None
