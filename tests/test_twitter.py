"""X / Twitter：syndication 和 fxtwitter 两种返回 -> VideoInfo。"""

import pytest

from parse_video_py.parser.twitter import Twitter, _from_fxtwitter, _from_syndication


def _variants(*pairs):
    return [{"content_type": ct, "url": url, "bitrate": br} for ct, url, br in pairs]


def test_syndication_video_takes_highest_bitrate_mp4():
    tweet = {
        "text": "hi",
        "user": {"id_str": "1", "name": "", "screen_name": "jack", "profile_image_url_https": "a.jpg"},
        "mediaDetails": [
            {"type": "photo", "media_url_https": "p.jpg"},
            {
                "type": "video",
                "media_url_https": "cover.jpg",
                "original_info": {"width": 1280, "height": 720},
                "video_info": {
                    "duration_millis": 4500,
                    "variants": _variants(
                        ("application/x-mpegURL", "m.m3u8", 0), ("video/mp4", "low.mp4", 1), ("video/mp4", "hi.mp4", 9)
                    ),
                },
            },
        ],
    }
    info = _from_syndication(tweet)
    assert (info.video_url, info.cover_url, info.images) == ("hi.mp4", "cover.jpg", [])
    assert (info.duration, info.width, info.height) == (4.5, 1280, 720)
    assert info.author.name == "jack"  # 没有显示名就用 screen_name


def test_syndication_top_level_video_and_photos():
    top = {"video": {"poster": "poster.jpg", "variants": _variants(("video/mp4", "top.mp4", 1))}, "mediaDetails": []}
    assert (_from_syndication(top).video_url, _from_syndication(top).cover_url) == ("top.mp4", "poster.jpg")

    photos = {
        "mediaDetails": [{"type": "photo", "media_url_https": "1.jpg"}, {"type": "photo", "media_url_https": "2.jpg"}]
    }
    info = _from_syndication(photos)
    assert [i.url for i in info.images] == ["1.jpg", "2.jpg"] and info.cover_url == "1.jpg"

    with pytest.raises(Exception, match="没有找到视频或图片"):
        _from_syndication({"mediaDetails": [{"type": "photo"}]})


def test_fxtwitter():
    video = {"media": {"all": [{"type": "photo", "url": "p.jpg"}, {"type": "gif", "thumbnail_url": "t.jpg",
             "variants": _variants(("video/mp4", "g.mp4", 1)), "duration": 2, "width": 10, "height": 20}]}}  # fmt: skip
    info = _from_fxtwitter(video)
    assert (info.video_url, info.cover_url, info.images, info.duration) == ("g.mp4", "t.jpg", [], 2.0)

    photos = _from_fxtwitter({"media": {"all": [{"type": "photo", "url": "p.jpg"}]}, "author": {"screen_name": "x"}})
    assert photos.cover_url == "p.jpg" and photos.author.name == "x"
    assert _from_fxtwitter({"media": {"all": []}}) is None


def test_tweet_id_and_token():
    assert Twitter._extract_tweet_id("https://x.com/jack/status/20?s=1") == "20"
    assert Twitter._extract_tweet_id("https://mobile.twitter.com/a/statuses/123") == "123"
    with pytest.raises(ValueError):
        Twitter._extract_tweet_id("https://x.com/jack")
    assert Twitter._get_token("1234567890123456789") == "387859413969726"  # 和 X 前端同一个算法
