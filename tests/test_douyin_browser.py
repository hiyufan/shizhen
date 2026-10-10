"""抖音图文兜底浏览器：轮询到图齐就返回、页面重载不算坏、并发上限与用完即关。不起真浏览器。"""

import asyncio
import contextlib

import pytest

from parse_video_py.parser import douyin
from parse_video_py.parser.douyin import DouYin, _WarmBrowser


class FakePage:
    def __init__(self, doms=()):
        self.doms = list(doms)
        self.waited = 0.0
        self.closed = False
        self.url = "about:blank"

    async def evaluate(self, _script):
        item = self.doms.pop(0) if len(self.doms) > 1 else self.doms[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def wait_for_timeout(self, ms):
        self.waited += ms / 1000

    async def goto(self, url, **_kw):
        self.url = url

    async def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


def _dom(n, total, detail=True):
    return {"images": [f"https://p3/{i}" for i in range(n)], "total": total, "detail": detail}


def test_returns_as_soon_as_all_images_are_there():
    page = FakePage([_dom(0, None), _dom(2, 6), _dom(6, 6)])
    dom = asyncio.run(DouYin._extract_note_dom(page))
    assert len(dom["images"]) == 6
    # 以前固定等 1 秒 + 两轮 1.5 秒；现在每轮只等 0.2 秒
    assert page.waited == pytest.approx(0.4)


def test_page_reload_mid_poll_is_not_a_browser_failure():
    reload = Exception("Page.evaluate: Execution context was destroyed, most likely because of a navigation")
    page = FakePage([reload, _dom(1, 1)])
    assert len(asyncio.run(DouYin._extract_note_dom(page))["images"]) == 1


def test_other_evaluate_errors_still_raise():
    page = FakePage([RuntimeError("Target crashed")])
    with pytest.raises(RuntimeError):
        asyncio.run(DouYin._extract_note_dom(page))


def test_aweme_info_without_images_returns_immediately():
    page = FakePage([_dom(0, 0)])
    dom = asyncio.run(DouYin._extract_note_dom(page))
    assert dom["total"] == 0 and page.waited == 0


def test_note_not_found_page_gives_up_at_once(monkeypatch):
    """要登录的视频走 /note/ 地址只显示「图文不存在」：一轮就交回原错，不再重开一次白等 15 秒。"""
    page = FakePage([{**_dom(0, None, detail=False), "gone": True}])
    opened = []

    class Browser:
        @contextlib.asynccontextmanager
        async def page(self):
            opened.append(1)
            yield page

    monkeypatch.setattr(douyin, "_warm_browser", Browser())
    assert asyncio.run(DouYin()._note_via_browser("1")) is None
    assert opened == [1] and page.waited == 0


class FakeContext:
    def __init__(self):
        self.pages = []

    async def new_page(self):
        self.pages.append(FakePage())
        return self.pages[-1]


@pytest.fixture
def pool(monkeypatch):
    wb = _WarmBrowser(2)
    ctx = FakeContext()

    async def ensure():
        return ctx

    monkeypatch.setattr(wb, "_ensure_context", ensure)
    return wb, ctx


def test_at_most_two_pages_at_once_and_each_closed_after_use(pool):
    wb, ctx = pool
    active, peak = 0, 0

    async def use():
        nonlocal active, peak
        async with wb.page():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    async def run():
        await asyncio.gather(*(use() for _ in range(5)))

    asyncio.run(run())
    assert peak == 2, "同时最多两个页面"
    # 停在抖音页面上会一直烧 CPU、切空白页内存又不还，所以用完就关
    assert len(ctx.pages) == 5 and all(p.closed for p in ctx.pages)


def test_failed_page_is_closed_and_error_propagates(pool):
    wb, ctx = pool

    async def run():
        with pytest.raises(TimeoutError):
            async with wb.page():
                raise TimeoutError("goto timed out")

    asyncio.run(run())
    assert ctx.pages[0].closed


def test_default_pool_size_from_env():
    assert douyin._BROWSER_PAGES >= 1
