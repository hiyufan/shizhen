"""皮皮虾的下载档可能比 video_high 清晰；同作者评论不等于同一作品。"""

from qiuyuantu.parser.base import VideoInfo
from qiuyuantu.parser.pipixia import PiPiXia, _formats
from qiuyuantu.parser.quality import apply_video_formats


def _video(url, *, ident="v-original", width=540, height=864):
    return {
        "video_id": ident,
        "video_high": {"uri": ident, "width": width, "height": height, "url_list": [{"url": url}]},
    }


async def test_download_variant_higher_than_video_high_is_retained_without_guessing_its_label():
    video = _video("preview.mp4")
    video["video_download"] = {"uri": "v-original", "width": 720, "height": 1152, "url_list": [{"url": "source.mp4"}]}
    info = apply_video_formats(VideoInfo("", ""), await _formats({"author": {"id": 1}, "video": video}))
    assert info.video_url == "source.mp4" and (info.width, info.height) == (720, 1152)
    assert info.formats[0].url == "preview.mp4"


async def test_same_author_different_video_comment_does_not_replace_the_requested_work():
    item = {
        "author": {"id": 1},
        "video": _video("requested.mp4"),
        "comments": [
            {
                "item": {
                    "author": {"id": 1},
                    "video": _video("unrelated-4k.mp4", ident="v-other", width=3840, height=2160),
                }
            }
        ],
    }
    info = apply_video_formats(VideoInfo("", ""), await _formats(item))
    assert info.video_url == "requested.mp4" and not info.formats


async def test_comment_without_media_identity_is_not_assumed_to_be_the_same_video():
    item = {
        "author": {"id": 1},
        "video": _video("requested.mp4"),
        "comments": [{"item": {"author": {"id": 1}, "video": {"video_high": {"url_list": [{"url": "unproven.mp4"}]}}}}],
    }
    assert [fmt.url for fmt in await _formats(item)] == ["requested.mp4"]


async def test_matching_cover_id_is_not_accepted_as_matching_video_identity():
    def video(url):
        return {"video_cover_image": {"uri": "same-cover"}, "video_high": {"url_list": [{"url": url}]}}

    item = {
        "author": {"id": 1},
        "video": video("requested.mp4"),
        "comments": [{"item": {"author": {"id": 1}, "video": video("unrelated.mp4")}}],
    }
    assert [fmt.url for fmt in await _formats(item)] == ["requested.mp4"]


async def test_video_cover_is_not_misinterpreted_as_an_8k_video_and_empty_download_is_not_invented():
    video = _video("requested.mp4")
    video["video_cover_image"] = {"width": 8192, "height": 8192, "url_list": [{"url": "cover.jpg"}]}
    video["video_download"] = {"width": 2160, "height": 3840, "url_list": []}
    assert [fmt.url for fmt in await _formats({"author": {"id": 1}, "video": video})] == ["requested.mp4"]


async def test_direct_item_url_uses_its_id_without_requiring_another_redirect(monkeypatch):
    async def parse(self, ident):
        assert ident == "123"
        return VideoInfo("video.mp4", "")

    monkeypatch.setattr(PiPiXia, "parse_video_id", parse)
    assert (await PiPiXia().parse_share_url("https://h5.pipix.com/item/123/?share=1")).video_url == "video.mp4"
