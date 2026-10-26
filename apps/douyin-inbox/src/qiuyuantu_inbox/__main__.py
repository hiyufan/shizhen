"""私信工作流的本机运行与运维入口。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import logging
import os
import signal
import time
from pathlib import Path
from urllib.parse import urlparse

from .models import Settings
from .paths import account_root
from .store import Store


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="持续收件并运行队列")
    run.add_argument("--send", action="store_true", help="启用回传；不传时只收件入队")
    scope = run.add_mutually_exclusive_group(required=True)
    scope.add_argument("--pilot-current", action="store_true", help="只处理浏览器当前选中的会话")
    scope.add_argument("--all", action="store_true", help="处理所有一对一会话的新媒体请求")
    commands.add_parser("status", help="查看队列、心跳、积压和暂停原因")
    commands.add_parser("pause", help="暂停收件、解析和回传")
    commands.add_parser("resume", help="处理异常后恢复工作流")
    commands.add_parser("follow-pause", help="只暂停自动回关，保留媒体工作流")
    commands.add_parser("follow-resume", help="恢复后续回关；未知结果仍只查询，不重发")
    jobs = commands.add_parser("jobs", help="查看任务编号和状态，不输出原始消息与地址")
    jobs.add_argument(
        "--state",
        choices=(
            "queued",
            "processing",
            "ready",
            "sending",
            "sent",
            "uncertain",
            "failed",
            "rejected",
            "waiting",
            "blocked",
            "cancelled",
        ),
    )
    resolve = commands.add_parser("resolve", help="人工核对不确定的发送结果")
    resolve.add_argument("job")
    resolve.add_argument("decision", choices=("delivered", "abandon"))
    return result


@contextlib.contextmanager
def account_lock(root: Path):
    with (root / "reply-test.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("同一账号已有收发任务在运行") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


async def run(store: Store, args) -> None:
    if store.settings.auto_follow and (not args.send or args.pilot_current):
        raise ValueError("自动回关仅可与 --all --send 一起启用")

    from playwright.async_api import async_playwright

    from qiuyuantu.convert import net, relay
    from qiuyuantu.parser.douyin import aclose_browser

    from .browser import Browser
    from .engine import Engine

    endpoint = os.environ.get("PARSE_VIDEO_INBOX_CDP_URL", "http://127.0.0.1:9223")
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("浏览器控制连接只允许本机地址")
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
            pages = [page for context in browser.contexts for page in context.pages]
            page = next((page for page in pages if page.url.startswith("https://www.douyin.com/")), None)
            if page is None:
                raise RuntimeError("请先登录独立账号浏览器")
            adapter = Browser(page, store.settings, store.get_meta("account"))
            current = await adapter.active()
            saved_scope = json.loads(store.get_meta("pilot_scope", "[]"))
            if args.pilot_current and not current and not saved_scope:
                raise RuntimeError("试运行前请在浏览器打开目标会话")
            identity = current or (await adapter.conversations())[0]
            adapter.check_account(identity)
            store.bind(identity["account"], time.time())
            adapter.account = identity["account"]
            store.recover(time.time())
            scope = set(saved_scope or [current["id"]]) if args.pilot_current else set()
            if args.pilot_current:
                store.set_meta("pilot_scope", json.dumps(sorted(scope)))
            store.configure_scope("pilot" if scope else "all", time.time())
            store.set_meta("sending_enabled", str(args.send))
            engine = Engine(store, adapter, scope, args.send)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, engine.stopped.set)
            await engine.processor.startup_cleanup()
            print(json.dumps({"running": True, "scope": "pilot" if scope else "all", "sending": args.send}), flush=True)
            await run_with_warm_parser(engine)
    finally:
        await aclose_browser()
        await net.aclose_pool()
        await relay.aclose_shared()


async def run_with_warm_parser(engine) -> None:
    from qiuyuantu.parser.douyin import warmup_browser

    if not engine.send_enabled:
        await engine.run()
        return
    # 匿名解析浏览器与私信登录态隔离；提前完成进程启动和首轮导航，收件不等待预热。
    warmup = asyncio.create_task(warmup_browser())
    try:
        await engine.run()
    finally:
        warmup.cancel()
        await asyncio.gather(warmup, return_exceptions=True)


def main() -> None:
    args = parser().parse_args()
    os.umask(0o077)
    root = account_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["PARSE_VIDEO_DATA_DIR"] = str(root / "parse-work")
    settings = Settings.from_env()
    store = Store(root / "workflow", settings)
    if args.command == "run":
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        with account_lock(root):
            asyncio.run(run(store, args))
    elif args.command == "status":
        print(json.dumps(store.status(), ensure_ascii=False, indent=2))
    elif args.command == "jobs":
        print(json.dumps(store.list_jobs(args.state), ensure_ascii=False, indent=2))
    elif args.command == "resolve":
        store.resolve(args.job, args.decision, time.time())
        print(json.dumps({"resolved": True}))
    elif args.command in {"follow-pause", "follow-resume"}:
        store.set_meta("follow_paused", "operator" if args.command == "follow-pause" else "")
        print(json.dumps({"follow_paused": args.command == "follow-pause"}))
    else:
        store.set_meta("paused", "operator" if args.command == "pause" else "")
        print(json.dumps({"paused": args.command == "pause"}))


if __name__ == "__main__":
    main()
