"""小红书：页面里的笔记数据 -> VideoInfo（视频档位、原图、实况）。"""

from parse_video_py.parser.redbook import RedBook


def _h264(w, h, url, **kw):
    return {"width": w, "height": h, "masterUrl": url, **kw}


VIDEO_NOTE = {
    "title": "",
    "desc": "视频笔记" * 20,
    "user": {"userId": "u1", "nickname": "作者", "avatar": "a.jpg"},
    "imageList": [{"urlDefault": "http://sns-webpic-qc.xhscdn.com/2024/abc/cover!nd_dft"}],
    "video": {
        "capa": {"duration": 33},
        "media": {
            "stream": {
                "h264": [
                    _h264(720, 1280, "v720", size=10, videoBitrate=1),
                    _h264(1080, 1920, "v1080a", duration=12000, videoBitrate=1),
                    _h264(1080, 1920, "v1080b", duration=12000, videoBitrate=9),
                    _h264(720, 1280, "v720dup"),
                    {"width": 1, "height": 1},
                ],
                "h265": [{"width": 1080, "height": 1920}, _h264(1080, 1920, "v265", size=7)],
            }
        },
    },
}

IMAGE_NOTE = {
    "title": "图文",
    "user": {"userId": "u2", "nickName": "昵称"},
    "imageList": [
        {"urlDefault": "http://sns-webpic-qc.xhscdn.com/2024/abc/notes_pre_post/tok1!nd_dft_wlteh_jpg_3"},
        {
            "fileId": "fid2",
            "urlDefault": "http://x/y/z",
            "livePhoto": True,
            "stream": {"h264": [{"masterUrl": "live2"}]},
        },
        {"urlDefault": "http://a/b"},
        {"livePhoto": True},
    ],
}


def _build(data, key="urlDefault", nick="nickname"):
    return RedBook()._build(data, image_url_key=key, nick_key=nick)


def test_video_note_picks_largest_h264_and_lists_other_tracks():
    info = _build(VIDEO_NOTE)
    assert info.video_url == "v1080b"
    assert (info.width, info.height, info.duration) == (1080, 1920, 12.0)
    assert [(f.label, f.url, f.codec) for f in info.formats] == [("720p", "v720", ""), ("1080p H.265", "v265", "H.265")]
    assert info.images == []
    assert info.cover_url == "http://sns-webpic-qc.xhscdn.com/2024/abc/cover!nd_dft"
    assert info.title == ("视频笔记" * 20)[:60]
    assert (info.author.uid, info.author.name, info.author.avatar) == ("u1", "作者", "a.jpg")


def test_image_note_uses_original_images_and_live_photos():
    info = _build(IMAGE_NOTE, key="url", nick="nickName")
    assert info.video_url == "" and info.formats == []
    assert [(i.url, i.live_photo_url) for i in info.images] == [
        ("https://ci.xiaohongshu.com/notes_pre_post/tok1?imageView2/2/w/0/format/jpg/q/90", ""),
        ("https://ci.xiaohongshu.com/fid2?imageView2/2/w/0/format/jpg/q/90", "live2"),
        ("http://a/b", ""),
    ]
    assert info.author.name == "昵称"


def test_video_duration_falls_back_to_capa():
    data = {**VIDEO_NOTE, "video": {"capa": {"duration": 33}, "media": {"stream": {"h264": [_h264(1, 2, "v")]}}}}
    assert _build(data).duration == 33.0
