"""限流：IPv6 按 /64 合并、全站总量上限。"""

import pytest
from fastapi import HTTPException

from parse_video_py.web import limits


@pytest.mark.parametrize(
    ("ip", "key"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2a04:4e41:24:1::5", "2a04:4e41:24:1::/64"),
        ("2a04:4e41:24:1:ffff:1:2:3", "2a04:4e41:24:1::/64"),  # 同一个 /64 里换地址，键不变
        ("::ffff:203.0.113.7", "203.0.113.7"),  # IPv4 映射地址按 IPv4 算
        ("unknown", "unknown"),
    ],
)
def test_limit_key(ip, key):
    assert limits.limit_key(ip) == key


def test_rotating_ipv6_inside_one_64_shares_a_bucket():
    rl = limits.RateLimit("t", 3, 60)
    for i in range(3):
        rl.hit(f"2001:db8:1:2::{i + 1}")
    with pytest.raises(HTTPException) as err:
        rl.hit("2001:db8:1:2::99")
    assert err.value.status_code == 429
    rl.hit("2001:db8:1:3::1")  # 另一个 /64 不受影响


def test_global_bucket_ignores_who_is_asking():
    rl = limits.RateLimit("t", 2, 60)
    rl.hit(limits.GLOBAL)
    rl.hit(limits.GLOBAL)
    with pytest.raises(HTTPException) as err:
        rl.hit(limits.GLOBAL, "现在解析的人太多了，{wait} 秒后再试")
    assert "太多" in err.value.detail


def test_concurrency_by_64_and_global():
    per_ip = limits.Concurrency("下载", 1)
    per_ip.acquire("2001:db8::1")
    with pytest.raises(HTTPException):
        per_ip.acquire("2001:db8::2")  # 同一个 /64
    per_ip.release("2001:db8::1")
    per_ip.acquire("2001:db8::2")

    total = limits.Concurrency("下载", 1, busy="现在下载的人太多了，稍等一下再试")
    total.acquire(limits.GLOBAL)
    with pytest.raises(HTTPException) as err:
        total.acquire(limits.GLOBAL)
    assert "太多" in err.value.detail


def test_limit_of_one_allows_the_first_request():
    rl = limits.RateLimit("t", 1, 60)
    rl.hit("203.0.113.7")
    with pytest.raises(HTTPException):
        rl.hit("203.0.113.7")
