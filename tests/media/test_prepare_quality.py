"""明确选择的高清源不能在转换准备时悄悄退成低档。"""

import pytest

from qiuyuantu.convert import bilibili, config, tasks
from qiuyuantu.convert.fetch import TooLarge


@pytest.fixture
def downloader(monkeypatch, tmp_path):
    streams = {
        "video": [
            {"height": 2160, "bandwidth": 16000, "codecid": 7},
            {"height": 1080, "bandwidth": 8000, "codecid": 7},
            {"height": 720, "bandwidth": 400, "codecid": 7},
        ]
    }
    monkeypatch.setattr(config, "SOURCES_DIR", tmp_path)
    monkeypatch.setattr(config, "SOURCE_MAX_HEIGHT", 1080)

    async def download(_job, _page, spec, directory, stem, *, fit):
        video, _ = bilibili.pick(streams, spec, 10, 1000, fit=fit)
        path = directory / (stem + ".mp4")
        path.write_text(str(video["height"]))
        return path

    monkeypatch.setattr(tasks, "_ytdlp", download)
    return streams


async def test_explicit_4k_does_not_fall_back_to_a_smaller_video_to_fit(downloader, tmp_path):
    with pytest.raises(TooLarge):
        await tasks._download_source_file(
            None, "selected", "", {}, "https://www.bilibili.com/video/example", "bv*[height=2160]+ba"
        )
    assert not list(tmp_path.iterdir())


async def test_missing_selected_tier_is_not_replaced_with_a_lower_one(downloader, tmp_path):
    downloader["video"] = downloader["video"][1:]
    with pytest.raises(RuntimeError, match="所选清晰度"):
        await tasks._download_source_file(
            None, "selected", "", {}, "https://www.bilibili.com/video/example", "bv*[height=2160]+ba"
        )
    assert not list(tmp_path.iterdir())


async def test_legacy_request_without_a_selection_can_still_fit_a_smaller_source(downloader):
    path = await tasks._download_source_file(None, "default", "", {}, "https://www.bilibili.com/video/example", "")
    assert path.read_text() == "720"
