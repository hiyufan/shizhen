"""仅对指定会话中的「求原图测试」执行一次图集回传。"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MARKER = "求原图测试"
MESSAGES = '[data-e2e="msg-item-content"]'
EDITOR = '[data-e2e="msg-input"] [contenteditable="true"]'
MAX_IMAGES = 9
MAX_FILE_BYTES = 20 << 20
MAX_TOTAL_BYTES = 100 << 20


def work_url(url: str, photos: bool) -> str:
    parsed = urlparse(url)
    work_id = parse_qs(parsed.query).get("modal_id", [""])[0]
    if parsed.scheme != "https" or parsed.netloc != "www.douyin.com" or not work_id.isascii() or not work_id.isdigit():
        raise RuntimeError("转发卡片没有打开有效的抖音作品。")
    return f"https://www.douyin.com/{'note' if photos else 'video'}/{work_id}"


def run_directory(path: Path, conversation: str, url: str) -> Path:
    key = hashlib.sha256(f"{conversation}\0{url}".encode()).hexdigest()
    return path / "test-runs" / key


def save_receipt(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@contextlib.contextmanager
def account_lock(path: Path):
    with (path / "reply-test.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("已有账号测试正在执行。") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


async def ensure_chat(page, conversation: str) -> None:
    heading = page.locator(".StackLayoutStackChatHeadertitle").filter(visible=True)
    if await heading.count() != 1 or (await heading.inner_text()).strip() != conversation:
        raise RuntimeError("当前会话与指定收件人不同，停止发送。")
    draft = (await page.locator(EDITOR).inner_text()).replace("\u200b", "").strip()
    if draft:
        raise RuntimeError("输入框已有草稿，停止发送。")


async def select_conversation(page, conversation: str) -> None:
    # 已打开的聊天层会挡住会话列表；不强制点击被遮挡的其他会话。
    heading = page.locator(".StackLayoutStackChatHeadertitle").filter(visible=True)
    if await heading.count():
        await ensure_chat(page, conversation)
        return
    panel = page.locator("[class*=StackLayoutStackLayoutV2wrapper]").filter(visible=True)
    if not await panel.is_visible():
        await page.get_by_text("消息", exact=True).filter(visible=True).first.click()
    item = panel.locator('[data-e2e="conversation-item"]').filter(has=page.get_by_text(conversation, exact=True))
    if await item.count() != 1:
        raise RuntimeError("未找到唯一匹配的会话，不选择同名收件人。")
    await item.click(timeout=5000)
    await page.locator(EDITOR).wait_for(state="visible")
    await ensure_chat(page, conversation)


async def open_test_card(page, conversation: str) -> str:
    await select_conversation(page, conversation)
    marker = page.locator(MESSAGES).filter(has=page.get_by_text(MARKER, exact=True))
    if await marker.count() != 1:
        raise RuntimeError("会话需要恰好一条「求原图测试」；请紧接在转发作品后发送。")
    if "isFromMe" in (await marker.locator("../..").get_attribute("class") or ""):
        raise RuntimeError("测试口令必须由对方发来。")
    row = marker.locator("xpath=ancestor::div[@data-index][1]")
    index = int(await row.get_attribute("data-index"))
    card = page.locator(f'[data-index="{index + 1}"] {MESSAGES} .MessageItemShareAwemecontainer')
    if await card.count() != 1:
        raise RuntimeError("测试口令前一条需要是转发作品卡片。")
    photos = await card.locator(".MessageItemShareAwemephotosTag").count() > 0
    await card.click()
    try:
        await page.wait_for_url("**/*modal_id=*", timeout=15000)
        return work_url(page.url, photos)
    finally:
        await page.keyboard.press("Escape")


def prepare_image(source: Path) -> Path:
    from PIL import Image

    with Image.open(source) as image:
        if image.width * image.height > 40_000_000:
            raise RuntimeError("图片像素超过测试上限。")
        extension = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif"}.get(image.format)
        if extension:
            image.verify()
            target = source.with_suffix(extension)
            source.replace(target)
        elif image.format == "WEBP" and not getattr(image, "is_animated", False):
            target = source.with_suffix(".png")
            image.save(target, format="PNG")
            source.unlink()
        else:
            raise RuntimeError("该图片格式尚未验证，保留源文件，停止回传。")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise RuntimeError("图片超过测试上传大小限制。")
    return target


async def prepare_images(url: str, directory: Path) -> list[Path]:
    from qiuyuantu.convert.fetch import fetch_bytes
    from qiuyuantu.parser import parse_video_share_url

    info = await asyncio.wait_for(parse_video_share_url(url), timeout=90)
    if not 1 <= len(info.images) <= MAX_IMAGES:
        raise RuntimeError("测试命令目前只回传 1–9 张图片的图集，视频需另行验证。")
    files = []
    for index, image in enumerate(info.images, 1):
        source = directory / f"image-{index:02d}.source"
        await asyncio.wait_for(fetch_bytes(image.url, source, limit=MAX_FILE_BYTES), timeout=60)
        files.append(await asyncio.to_thread(prepare_image, source))
        if sum(file.stat().st_size for file in files) > MAX_TOTAL_BYTES:
            raise RuntimeError("整组图片超过测试上传大小限制。")
    return files


async def send_images(page, conversation: str, files: list[Path]) -> None:
    await ensure_chat(page, conversation)
    await page.locator('[data-e2e="msg-input"] input.semi-upload-hidden-input').set_input_files(files)
    dialog = page.get_by_role("dialog").filter(has_text=files[0].name)
    await dialog.wait_for(state="visible", timeout=15000)
    if conversation not in await dialog.inner_text():
        raise RuntimeError("文件确认窗口的收件人不符，停止发送。")
    await ensure_chat(page, conversation)
    await dialog.get_by_role("button", name="发送", exact=True).click()
    await dialog.wait_for(state="hidden", timeout=20000)
    # 已送达的上传图片仍可能使用本地 blob 预览，不能用地址是否变成 https 判断发送状态。
    await page.wait_for_function(
        """count => {
          const rows = [...document.querySelectorAll('[data-e2e="msg-item-content"]')].slice(0, count);
          return rows.length === count && rows.every(e =>
            e.parentElement.parentElement.className.includes('isFromMe') &&
            !e.parentElement.innerText.includes('发送中') &&
            !e.parentElement.innerText.includes('失败') &&
            [...e.querySelectorAll('img.MessageItemImageImage')].some(i =>
              i.complete && i.naturalWidth > 0));
        }""",
        arg=len(files),
        timeout=60000,
    )


async def send_reply(page, conversation: str, text: str) -> None:
    await ensure_chat(page, conversation)
    await page.locator(EDITOR).fill(text)
    await page.locator('[data-e2e="msg-input"] .e2e-send-msg-btn').click()
    await page.wait_for_function(
        """text => {
          const e = document.querySelector('[data-e2e="msg-item-content"]');
          return e && e.innerText === text &&
            e.parentElement.parentElement.className.includes('isFromMe') &&
            !e.parentElement.innerText.includes('发送中') && !e.parentElement.innerText.includes('失败');
        }""",
        arg=text,
        timeout=30000,
    )


async def reply_in_browser(page, path: Path, conversation: str) -> dict:
    url = await open_test_card(page, conversation)
    directory = run_directory(path, conversation, url)
    receipt = directory / "receipt.json"
    if receipt.exists():
        return {"status": "already_recorded", "state": json.loads(receipt.read_text())["state"]}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = await prepare_images(url, directory)
    await ensure_chat(page, conversation)
    # 发消息前落盘：超时或进程退出也不自动重发，避免收件人收到重复素材。
    data = {"state": "prepared", "url": url, "files": [file.name for file in files]}
    save_receipt(receipt, data)
    await send_images(page, conversation, files)
    data["state"] = "images_sent"
    save_receipt(receipt, data)
    await send_reply(page, conversation, f"求原图测试：已解析并回传 {len(files)} 张图片，私信保存后请核对清晰度。")
    data["state"] = "sent"
    save_receipt(receipt, data)
    return {"status": "sent", "images": len(files)}


async def reply_test(path: Path, conversation: str) -> dict:
    from playwright.async_api import async_playwright

    # 解析下载使用原有安全客户端；账号 Cookie 留在网页登录会话中。
    from qiuyuantu.convert import net, relay
    from qiuyuantu.parser.douyin import aclose_browser

    from .account import CDP_URL, HOME_URL

    with account_lock(path):
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(CDP_URL, timeout=10000)
                pages = [page for context in browser.contexts for page in context.pages]
                page = next((page for page in pages if page.url.startswith(HOME_URL)), None)
                if page is None:
                    raise RuntimeError("请先启动账号浏览器并登录。")
                return await reply_in_browser(page, path, conversation)
        finally:
            await aclose_browser()
            await net.aclose_pool()
            await relay.aclose_shared()
