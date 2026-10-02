"""快手落地页 INIT_STATE → VideoInfo：视频、多图图集、单图作品。数据按真实页面裁剪，不碰网络。"""

from parse_video_py.parser.kuaishou import KuaiShou


def _state(photo):
    return {"tmp_abc": {"result": 1, "photo": photo}}


COVER = "http://ws2.a.kwimgs.com/upic/2026/10/01/16/BMj_cover.jpg?clientCacheKey=3x8j.jpg"


def test_single_picture_post_returns_its_image_and_music():
    # v.kuaishou.com/n86DkRPN：photoType SINGLE_PICTURE，mainMvUrls 为空、没有 atlas，以前报「没拿到任何视频或图片」
    info = KuaiShou._build(_state({
        "photoType": "SINGLE_PICTURE", "singlePicture": True, "mainMvUrls": [], "caption": "单图",
        "coverUrls": [{"cdn": "ws2.a.kwimgs.com", "url": COVER}], "width": 720, "height": 1280,
        "ext_params": {"single": {"music": "/ufile/atlas/x.m4a",
                                  "musicCdnList": [{"cdn": "txmov2.a.kwimgs.com"}], "cdnList": [{"cdn": "ws2.a.kwimgs.com"}]}},
    }))
    assert [i.url for i in info.images] == [COVER.replace("http://", "https://")]
    assert info.video_url == ""
    assert info.music_url == "https://txmov2.a.kwimgs.com/ufile/atlas/x.m4a"


def test_atlas_keeps_images_and_picks_up_music():
    info = KuaiShou._build(_state({
        "mainMvUrls": [{"url": "https://v.kwaicdn.com/shell.mp4"}],
        "coverUrls": [{"url": COVER}],
        "ext_params": {"atlas": {"cdn": ["p2.a.yximgs.com"], "list": ["/ufile/atlas/1.jpg", "/ufile/atlas/2.jpg"],
                                 "music": "/ufile/atlas/bgm.m4a", "musicCdnList": [{"cdn": "txmov2.a.kwimgs.com"}]}},
    }))
    assert len(info.images) == 2 and info.video_url == ""   # 图集的 mainMvUrls 是配乐视频壳
    assert info.music_url.endswith("/ufile/atlas/bgm.m4a")


def test_plain_video_is_unchanged():
    info = KuaiShou._build(_state({
        "photoType": "VIDEO", "mainMvUrls": [{"url": "https://v.kwaicdn.com/1.mp4"}],
        "coverUrls": [{"url": COVER}], "duration": 12000,
    }))
    assert info.video_url == "https://v.kwaicdn.com/1.mp4" and info.images == [] and info.music_url == ""


def test_web_and_landing_links_are_routed_to_kuaishou_by_photo_id():

    from parse_video_py.parser import VideoSource, detect_source
    from parse_video_py.parser.kuaishou import _PHOTO_ID

    pid = "3x7ryeb59738de4"
    for url in (f"https://www.kuaishou.com/short-video/{pid}?authorId=x",
                f"https://live.kuaishou.com/u/mayang9yc9/{pid}",
                f"https://c.kuaishou.com/fw/photo/{pid}?fid=1&cc=share_copylink",
                f"https://v.m.chenzhongtech.com/fw/long-video/{pid}"):
        assert detect_source(url) == VideoSource.KuaiShou, url
        assert _PHOTO_ID.search(url).group(1) == pid, url
    assert detect_source("https://v.kuaishou.com/JSZcf5hc") == VideoSource.KuaiShou
    assert _PHOTO_ID.search("https://www.kuaishou.com/new-reco") is None
