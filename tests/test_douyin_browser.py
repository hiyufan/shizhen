"""抖音图文兜底浏览器：轮询到图齐就返回、页面重载不算坏、并发上限与用完即关。不起真浏览器。"""

import asyncio
import contextlib

import pytest

from qiuyuantu.parser import douyin
from qiuyuantu.parser.douyin import DouYin, _WarmBrowser
from qiuyuantu.parser.errors import ParseError


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

    def on(self, _event, _handler):
        pass

    async def goto(self, url, **_kw):
        self.url = url

    async def wait_for_load_state(self, *_a, **_kw):
        pass

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
    """页面只显示「图文不存在」：一轮就给出没有公开的定论，不再重开一次白等 15 秒。"""
    page = FakePage([{**_dom(0, None, detail=False), "gone": True}])
    opened = []

    class Browser:
        @contextlib.asynccontextmanager
        async def page(self):
            opened.append(1)
            yield page

    monkeypatch.setattr(douyin, "_warm_browser", Browser())
    parser = DouYin()
    parser._note = True
    with pytest.raises(ParseError) as exc:
        asyncio.run(parser._note_via_browser("1"))
    assert exc.value.reason == "restricted" and "没有公开给未登录用户" in str(exc.value)
    assert "cookies" not in str(exc.value)
    assert opened == [1] and page.waited == 0


class FakeDocument:
    """主框架导航拿到的一份 HTML。"""

    def __init__(self, page, html, status=200):
        self.html, self.status, self.frame, self.request = html, status, page.main_frame, self

    def is_navigation_request(self):
        return True

    async def text(self):
        return self.html


class ChallengedPage(FakePage):
    """先给验证页；reload_after 秒后像真页面那样自己重载，再来一份文档。"""

    main_frame = object()

    def __init__(self, first, reloaded=None, reload_after=0.01, doms=()):
        super().__init__(doms or [_dom(1, 1)])
        self.first, self.reloaded, self.reload_after = first, reloaded, reload_after
        self.loaded = False

    def on(self, event, handler):
        assert event == "response"
        self.emit = handler

    async def goto(self, url, **_kw):
        self.url = url
        # 跳转的中间一跳和这次导航自己的文档，都在 goto 返回前就报上来了
        self.emit(FakeDocument(self, "", status=302))
        first = FakeDocument(self, self.first)
        self.emit(first)
        if self.reloaded is not None:
            asyncio.get_running_loop().call_later(self.reload_after, self.emit, FakeDocument(self, self.reloaded))
        return first

    async def wait_for_load_state(self, *_a, **_kw):
        self.loaded = True


def _note_html(video_id="1"):
    import json

    note = {"awemeId": video_id, "images": [{"uri": "a/b", "urlList": ["https://p3/a/b~q75.jpeg"]}]}
    payload = "5:" + json.dumps({"aweme": note}, separators=(",", ":"))
    return f"<script>self.__pace_f.push([1,{json.dumps(payload)}])</script>" + "x" * douyin._MIN_NOTE_HTML


def _open_note(monkeypatch, page):
    class Browser:
        @contextlib.asynccontextmanager
        async def page(self):
            yield page

    monkeypatch.setattr(douyin, "_warm_browser", Browser())
    return asyncio.run(asyncio.wait_for(DouYin()._open_note("1", "https://www.douyin.com/note/1", 0), 5))


def test_reloaded_html_after_verification_page_is_read_without_waiting_for_render(monkeypatch):
    page = ChallengedPage("<html>验证中</html>", _note_html())
    dom = _open_note(monkeypatch, page)
    assert dom["from_html"] and dom["images"] == ["https://p3/a/b~q75.jpeg"]
    assert not page.loaded and page.waited == 0, "没有再等页面加载，也没有去 DOM 里轮询"


def test_verification_page_that_never_reloads_falls_back_to_rendered_page(monkeypatch):
    monkeypatch.setattr(douyin, "_CHALLENGE_RELOAD_WAIT", 0.05)
    page = ChallengedPage("<html>验证中</html>")
    dom = _open_note(monkeypatch, page)
    assert page.loaded and len(dom["images"]) == 1 and not dom.get("from_html")


def test_reloaded_page_without_note_data_falls_back_to_rendered_page(monkeypatch):
    page = ChallengedPage("<html>验证中</html>", "<html>又一张验证页</html>")
    dom = _open_note(monkeypatch, page)
    assert page.loaded and not dom.get("from_html")


def test_full_page_without_note_data_does_not_wait_for_a_reload(monkeypatch):
    # 完整页面里没有这条图文（图文不存在、其实是视频）：不会有重载，别白等
    monkeypatch.setattr(douyin, "_CHALLENGE_RELOAD_WAIT", 60)
    page = ChallengedPage("x" * douyin._MIN_NOTE_HTML)
    dom = _open_note(monkeypatch, page)
    assert page.loaded and not dom.get("from_html")


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
