"""验证测试回传的收件人检查和不确定发送结果下的去重。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from qiuyuantu_inbox import reply_test as reply


@pytest.mark.parametrize(
    "url",
    [
        "http://www.douyin.com/jingxuan?modal_id=123",
        "https://www.douyin.com.evil.test/?modal_id=123",
        "https://www.douyin.com/?modal_id=１２３",
        "https://www.douyin.com/?modal_id=abc",
        "https://www.douyin.com/jingxuan",
    ],
)
def test_work_url_requires_real_platform_and_numeric_id(url):
    with pytest.raises(RuntimeError):
        reply.work_url(url, photos=True)


def test_work_url_preserves_card_type():
    url = "https://www.douyin.com/jingxuan?modal_id=123"
    assert reply.work_url(url, photos=True) == "https://www.douyin.com/note/123"
    assert reply.work_url(url, photos=False) == "https://www.douyin.com/video/123"


def test_run_key_separates_recipients_and_cannot_escape_directory(tmp_path):
    url = "https://www.douyin.com/note/123"
    first = reply.run_directory(tmp_path, "../test", url)
    assert first.parent == tmp_path / "test-runs"
    assert first != reply.run_directory(tmp_path, "other", url)


def test_prepare_image_preserves_original_jpeg_bytes(tmp_path):
    source = tmp_path / "image.source"
    Image.new("RGB", (12, 8), "blue").save(source, format="JPEG")
    original = source.read_bytes()
    target = reply.prepare_image(source)
    assert target.suffix == ".jpg"
    assert target.read_bytes() == original


def test_prepare_image_converts_static_webp_for_chat_upload(tmp_path):
    source = tmp_path / "image.source"
    Image.new("RGB", (12, 8), "blue").save(source, format="WEBP")
    target = reply.prepare_image(source)
    with Image.open(target) as image:
        assert image.format == "PNG"
        assert image.size == (12, 8)


async def test_wrong_chat_stops_before_touching_draft():
    heading = SimpleNamespace(count=AsyncMock(return_value=1), inner_text=AsyncMock(return_value="other"))
    heading.filter = lambda **kwargs: heading
    page = SimpleNamespace(locator=lambda selector: heading)
    with pytest.raises(RuntimeError, match="指定收件人"):
        await reply.ensure_chat(page, "test")


async def test_current_chat_is_reused_without_clicking_covered_list(monkeypatch):
    heading = SimpleNamespace(count=AsyncMock(return_value=1))
    heading.filter = lambda **kwargs: heading

    def locator(selector):
        assert selector == ".StackLayoutStackChatHeadertitle"
        return heading

    page = SimpleNamespace(locator=locator)
    check = AsyncMock()
    monkeypatch.setattr(reply, "ensure_chat", check)
    await reply.select_conversation(page, "test")
    check.assert_awaited_once_with(page, "test")


async def test_ambiguous_send_is_recorded_and_never_automatically_repeated(monkeypatch, tmp_path):
    url = "https://www.douyin.com/note/123"
    file = tmp_path / "image.jpg"
    file.write_bytes(b"prepared")
    monkeypatch.setattr(reply, "open_test_card", AsyncMock(return_value=url))
    prepare = AsyncMock(return_value=[file])
    monkeypatch.setattr(reply, "prepare_images", prepare)
    monkeypatch.setattr(reply, "ensure_chat", AsyncMock())
    send = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(reply, "send_images", send)
    with pytest.raises(TimeoutError):
        await reply.reply_in_browser(object(), tmp_path, "test")
    receipt = reply.run_directory(tmp_path, "test", url) / "receipt.json"
    assert json.loads(receipt.read_text())["state"] == "prepared"
    result = await reply.reply_in_browser(object(), tmp_path, "test")
    assert result == {"status": "already_recorded", "state": "prepared"}
    assert send.await_count == 1
    assert prepare.await_count == 1


def test_account_lock_prevents_concurrent_reply_runs(tmp_path):
    with reply.account_lock(tmp_path), pytest.raises(RuntimeError, match="正在执行"), reply.account_lock(tmp_path):
        pytest.fail("第二次账号锁不应获取成功")
