"""真实分享里出现过的链接样式 → 交给哪个解析器；只做路由，不发请求。

平台新增的链接样式常常不报错，而是悄悄落到 yt-dlp 的通用提取器里失败。每次从
`scripts/link_gaps.py` 的报告里补上一种样式，就把它加到这里，防止以后改路由时又漏掉。
各平台内部的细分（作品 / 目录 / 个人资料）在各自的测试里。
"""

import sqlite3
import time

import pytest

from qiuyuantu import link_gaps
from qiuyuantu.parser import VideoSource, detect_source, shortlinks
from qiuyuantu.parser.direct import direct_media

CATALOG = [
    # Steam：商店游戏页、点数商店、社区作品与个人资料、官方短链
    ("https://store.steampowered.com/app/730/CounterStrike_2/", VideoSource.Steam),
    ("https://store.steampowered.com/agecheck/app/292030/", VideoSource.Steam),
    ("https://store.steampowered.com/points/shop/app/3339420", VideoSource.Steam),
    ("https://store.steampowered.com/points/profile/76561199629689243/reward/100", VideoSource.Steam),
    ("https://steamcommunity.com/sharedfiles/filedetails/?id=2924226192", VideoSource.Steam),
    ("https://steamcommunity.com/id/gabelogannewell/", VideoSource.Steam),
    ("https://s.team/a/730", VideoSource.Steam),
    # Pixiv：主站、旧版地址、作者短链、预览镜像、原图代理与图片文件
    ("https://www.pixiv.net/en/artworks/151591483", VideoSource.Pixiv),
    ("https://www.pixiv.net/member_illust.php?mode=medium&illust_id=42", VideoSource.Pixiv),
    ("https://pixiv.me/someone", VideoSource.Pixiv),
    ("https://www.phixiv.net/artworks/151591483", VideoSource.Pixiv),
    ("https://ppxiv.net/artworks/151591483", VideoSource.Pixiv),
    ("https://pixiv.cat/151591483-1.png", VideoSource.Pixiv),
    ("https://i.pximg.net/img-master/img/2026/01/01/00/00/00/42_p0_master1200.jpg", VideoSource.Pixiv),
    ("https://sketch.pixiv.net/items/42", VideoSource.PixivSketch),
    # 微博：PC、手机、视频页、App 分享页与卡片页
    ("https://weibo.com/8016048326/5361270354346625", VideoSource.WeiBo),
    ("https://m.weibo.cn/status/5361595238580907", VideoSource.WeiBo),
    ("https://video.weibo.com/show?fid=1034:5075989772828781", VideoSource.WeiBo),
    ("https://mapp.api.weibo.cn/fx/d9bee5964c58c2e307da2feaec849357.html", VideoSource.WeiBo),
    ("http://weibo.com/p/2304445075180649906180", VideoSource.WeiBo),
    # 其他常见分享
    ("https://v.douyin.com/u3knSHnayKo/", VideoSource.DouYin),
    ("https://www.iesdouyin.com/share/video/7300000000000000000/", VideoSource.DouYin),
    ("https://b23.tv/xgrWG6H", VideoSource.BiliBili),
    ("http://xhslink.com/m/1gJ5tOG6M1b", VideoSource.RedBook),
    ("https://haokan.baidu.com/v?vid=12336643112805995867", VideoSource.HaoKan),
    ("https://x.com/a/status/1", VideoSource.Twitter),
    # Threads：帖子、旧版 /t/ 地址与 /share/ 分享短链（yt-dlp 没有 Threads 提取器）
    ("https://www.threads.com/@huiunyz/post/Dfbw28jkon0?xmt=AQG0", VideoSource.Threads),
    ("https://www.threads.net/t/Dfbw28jkon0", VideoSource.Threads),
    ("https://www.threads.com/share/_qnW5t1Du", VideoSource.Threads),
    # TikTok 走 yt-dlp；图文帖和空用户名的短链落地页在 tiktok.py 里补救
    ("https://www.tiktok.com/@/photo/7375688490052275488", VideoSource.YtDlp),
]


@pytest.mark.parametrize(("url", "source"), CATALOG)
def test_link_shapes_reach_their_parser(url, source):
    assert detect_source(url) == source


@pytest.mark.parametrize(
    "url",
    ["http://t.cn/A6RHfSQH", "https://bit.ly/x", "https://tinyurl.com/x", "https://www.is.gd/x"],
)
def test_generic_short_links_are_expanded_first(url):
    assert shortlinks.is_short(url)


@pytest.mark.parametrize(
    "url",
    [
        # 分段流清单交给 yt-dlp 合并，不当作单个文件
        "https://video.fastly.steamstatic.com/store_trailers/730/1/abc/hls_264_master.m3u8",
        "https://video.twimg.com/amplify_video/1/pl/master.mpd",
    ],
)
def test_stream_manifests_are_not_direct_files(url):
    assert direct_media(url) is None


def test_link_shape_hides_parameter_values():
    shape = link_gaps.link_shape("https://www.example.com/u/AbC123def4/app/730/Game_Name/?token=secret&b=1")
    assert shape == ("example.com", "/u/{id}/app/{n}/{slug}/?b&token")


def test_gap_report_groups_by_shape_and_skips_content_failures(tmp_path):
    path = tmp_path / "failures.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE failures (ts REAL, platform TEXT, reason TEXT, msg TEXT, link TEXT)")
        now = time.time()
        rows = [
            (now, "ytdlp", "unsupported", "还不支持", "https://store.steampowered.com/app/730/Counter_Strike_2/"),
            (now, "ytdlp", "unsupported", "还不支持", "https://store.steampowered.com/app/440/Team_Fortress_2/"),
            (now, "ytdlp", "unsupported", "还不支持", "https://store.steampowered.com/app/440/Team_Fortress_2/"),
            (now, "pixiv", "restricted", "R-18", "https://www.pixiv.net/artworks/1"),
            (now - 30 * 86400, "ytdlp", "unsupported", "很久以前", "https://old.example/x"),
        ]
        conn.executemany("INSERT INTO failures VALUES (?, ?, ?, ?, ?)", rows)
    report = link_gaps.gaps(path, days=7)
    # 内容本身的原因（R-18）和 7 天以前的记录不算缺格式
    assert len(report) == 1
    row = report[0]
    assert (row["host"], row["shape"], row["links"], row["count"]) == (
        "store.steampowered.com",
        "/app/{n}/{slug}/",
        2,
        3,
    )
    # 当前代码已经改交给 Steam 解析器
    assert row["now"] == "steam"
