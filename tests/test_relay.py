"""边缘中继：边缘取图地址。不碰外网。"""

import time

import httpx

from parse_video_py.convert import relay


def test_edge_image_url_is_stable_within_the_hour(monkeypatch):
    monkeypatch.setattr(relay, "RELAY_URL", "https://edge.example.com/relay")
    monkeypatch.setattr(relay, "RELAY_TOKEN", "tok")
    img = "https://sns-img-hw.xhscdn.com/abc"
    hour = 1_800_000_000 // 3600 * 3600
    monkeypatch.setattr(time, "time", lambda: hour + 10)
    first = relay.edge_img_url(img, 600)
    monkeypatch.setattr(time, "time", lambda: hour + 3500)
    assert relay.edge_img_url(img, 600) == first, "同一小时里地址要一样，浏览器缓存才用得上"
    # 不管落在一小时里的哪一秒，至少还能用 ttl 秒
    assert int(httpx.URL(first).params["e"]) >= hour + 3500 + 600
