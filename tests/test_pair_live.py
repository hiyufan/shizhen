"""平台自带实况的打包：几张一起拉，结果按原顺序；一张失败其余的别接着跑。"""

import asyncio
import io
import zipfile

import pytest
from PIL import Image

from parse_video_py.convert import tasks
from parse_video_py.convert.jobs import Job


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def fake_fetch(monkeypatch):
    """假的下载：记下同时在拉几个；图片给一张真 JPEG，视频给能认出是第几张的字节。"""
    state = {"running": 0, "peak": 0, "started": [], "fail": None}

    async def fetch(url, dest, headers=None, limit=0):
        state["started"].append(url)
        state["running"] += 1
        state["peak"] = max(state["peak"], state["running"])
        try:
            if url == state["fail"]:
                raise RuntimeError("拉取失败: ConnectTimeout")
            # 越靠前的越慢：顺序要靠 gather 保住，不能按完成先后排
            n = int(url.rsplit("-", 1)[1])
            await asyncio.sleep(0.05 / n)
            dest.write_bytes(_jpeg() if url.startswith("img") else f"video-{n}".encode())
        finally:
            state["running"] -= 1

    monkeypatch.setattr(tasks, "fetch_bytes", fetch)
    return state


def _items(n: int) -> list[dict]:
    return [{"image_url": f"img-{i}", "video_url": f"vid-{i}"} for i in range(1, n + 1)]


def test_photos_are_fetched_concurrently_and_kept_in_order(fake_fetch):
    job = Job(id="abc123", type="live")
    asyncio.run(tasks.pair_live(job, items=_items(5), fmt="motionphoto", title="t"))

    # 同一张的图和视频一起拉，几张之间也一起：最多 _LIVE_PARALLEL 张 × 2 个文件
    assert fake_fetch["peak"] == tasks._LIVE_PARALLEL * 2
    with zipfile.ZipFile(job.result_path) as zf:
        names = zf.namelist()
        assert names == [f"MVIMG_t_{i}.jpg" for i in range(1, 6)]
        # 动态照片把视频接在 JPEG 后面：第 i 个文件里装的就是第 i 段视频
        assert [zf.read(name).endswith(f"video-{i}".encode()) for i, name in enumerate(names, 1)] == [True] * 5
    assert job.extra == {"count": 5}


def test_one_failure_cancels_the_rest_with_the_original_error(fake_fetch):
    fake_fetch["fail"] = "vid-1"
    job = Job(id="def456", type="live")
    with pytest.raises(RuntimeError, match="ConnectTimeout"):
        asyncio.run(tasks.pair_live(job, items=_items(6), fmt="motionphoto", title="t"))
    # 第 1 张失败时后面排队的还没开始，就别再开始了
    assert "img-6" not in fake_fetch["started"]
    assert fake_fetch["running"] == 0
