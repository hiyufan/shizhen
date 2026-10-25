"""启动普通抖音账号的独立浏览器，供人工登录及私信能力验证使用。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import shutil
import signal
from pathlib import Path

from .paths import account_root

CDP_URL = "http://127.0.0.1:9223"
HOME_URL = "https://www.douyin.com/"


def account_dir() -> Path:
    """与解析器的匿名会话隔离，不导出或复用账号 Cookie。"""
    return account_root()


def prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def browser_options(path: Path, headed: bool) -> dict:
    options = {
        "user_data_dir": str(path / "profile"),
        "headless": not headed,
        "locale": "zh-CN",
        "viewport": {"width": 1360, "height": 900},
        "accept_downloads": False,
        "args": [
            "--disable-dev-shm-usage",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9223",
        ],
    }
    executable = os.environ.get("PARSE_VIDEO_ACCOUNT_BROWSER") or shutil.which("chromium-browser")
    if executable:
        options["executable_path"] = executable
    return options


def doctor() -> bool:
    checks = {
        "playwright": importlib.util.find_spec("playwright") is not None,
        "parser": importlib.util.find_spec("qiuyuantu.parser") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }
    print(json.dumps(checks, ensure_ascii=False))
    return all(checks.values())


async def show_login(page) -> None:
    # 验证页和扫码确认交给账号持有人；找不到入口时保留原页面供人工操作。
    login = page.get_by_text("登录", exact=True).first
    if await login.is_visible():
        await login.click(timeout=5000)


async def wait_for_stop(timeout: float) -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        if timeout:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopped.wait(), timeout=timeout)
        else:
            await stopped.wait()
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def login_browser(path: Path, headed: bool, timeout: float) -> None:
    from playwright.async_api import async_playwright

    prepare_directory(path)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(**browser_options(path, headed))
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            await show_login(page)
            print("浏览器已启动，等待人工登录；尚未启用私信收发。", flush=True)
            await wait_for_stop(timeout)
        finally:
            await context.close()


async def snapshot(path: Path) -> None:
    from playwright.async_api import async_playwright

    prepare_directory(path)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(CDP_URL, timeout=10000)
        pages = [page for context in browser.contexts for page in context.pages]
        page = next((page for page in pages if page.url.startswith(HOME_URL)), None)
        if page is None:
            raise RuntimeError("没有找到抖音页面，请先启动 login。")
        # 截图可能包含二维码或聊天，仅按需保存到私有目录，不写进 Web 静态目录。
        await page.screenshot(path=str(path / "screen.png"))
        print(json.dumps({"title": await page.title(), "screenshot": str(path / "screen.png")}, ensure_ascii=False))
        # 只断开本次连接；browser.close() 会关闭正在等待扫码的浏览器。


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "login", "snapshot", "reply-test"))
    parser.add_argument("--conversation", help="仅向这个显示名称唯一匹配的测试会话回传图片")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口，需要 DISPLAY")
    parser.add_argument("--timeout", type=float, default=1800, help="登录窗口保留秒数，0 为持续运行")
    args = parser.parse_args()
    if args.timeout < 0:
        parser.error("--timeout 必须大于等于 0")
    if args.command == "reply-test" and not args.conversation:
        parser.error("reply-test 需要 --conversation 指定测试收件人")
    os.umask(0o077)
    if args.command == "doctor":
        raise SystemExit(0 if doctor() else 1)
    if args.command == "reply-test":
        from .reply_test import reply_test

        path = account_dir()
        prepare_directory(path)
        os.environ["PARSE_VIDEO_DATA_DIR"] = str(path / "parse-work")
        print(json.dumps(asyncio.run(reply_test(path, args.conversation)), ensure_ascii=False))
    elif args.command == "login":
        asyncio.run(login_browser(account_dir(), args.headed, args.timeout))
    else:
        asyncio.run(snapshot(account_dir()))


if __name__ == "__main__":
    main()
