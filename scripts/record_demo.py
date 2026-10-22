"""录 README 开头的演示动图：粘贴链接 → 解析 → 结果 → 截一段转 GIF。

在应用容器里跑（Playwright 和 ffmpeg 都在里面），录的是本机服务，不进使用统计：

    docker compose cp scripts/record_demo.py app:/tmp/record_demo.py
    docker compose exec app python /tmp/record_demo.py /tmp/demo
    docker compose cp app:/tmp/demo/demo.webp docs/demo.webp

用 Chrome 开发者协议的录屏抓帧（Playwright 自带的录视频码率低，字是糊的），再用 ffmpeg 合成。
解析、准备原视频、生成 GIF 这几段等待快进播放，页面上的「已等待 N 秒」是真实时间。
演示视频是 Blender 基金会的开源电影 Big Buck Bunny（CC BY 3.0），换了视频要重新挑 CLIP。
"""

import asyncio
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

SITE = "http://127.0.0.1:8000/"
LINK = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
CLIP = (112, 116)  # 截哪几秒转 GIF：蝴蝶飞到兔子面前
VIEWPORT = {"width": 1280, "height": 800}
WAIT_PLAYS_FOR = 2.5  # 每段长时间等待在动图里最多播几秒
OUT_WIDTH = 960


class Recorder:
    """收录屏帧，记下哪几段要快进。"""

    def __init__(self, cdp):
        self.cdp = cdp
        self.frames: list[tuple[float, bytes]] = []
        self.fast: list[tuple[float, float]] = []

    async def start(self):
        self.cdp.on("Page.screencastFrame", self._frame)
        await self.cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 92, "maxWidth": VIEWPORT["width"], "maxHeight": VIEWPORT["height"]},
        )

    def _frame(self, event):
        self.frames.append((time.time(), base64.b64decode(event["data"])))
        asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]}))

    async def stop(self):
        await self.cdp.send("Page.stopScreencast")

    async def waiting(self, coro):
        """等一个慢操作，这段时间在动图里快进。"""
        started = time.time()
        result = await coro
        self.fast.append((started, time.time()))
        return result

    def timeline(self, end: float) -> list[tuple[bytes, float]]:
        """每帧在动图里停多久：快进段按比例压到 WAIT_PLAYS_FOR 秒，其余照实际时长。"""

        def played(a: float, b: float) -> float:
            total = b - a
            for s, e in self.fast:
                overlap = max(0.0, min(b, e) - max(a, s))
                if overlap:
                    rate = max(1.0, (e - s) / WAIT_PLAYS_FOR)
                    total -= overlap - overlap / rate
            return total

        times = [t for t, _ in self.frames] + [end]
        return [(img, played(times[i], times[i + 1])) for i, (_, img) in enumerate(self.frames)]


async def scroll_to(page, selector: str, offset: int = 90):
    await page.evaluate(
        """([sel, off]) => {
            const top = document.querySelector(sel).getBoundingClientRect().top + scrollY - off;
            scrollTo({top, behavior: 'smooth'});
        }""",
        [selector, offset],
    )
    await page.wait_for_timeout(1200)


async def pick_clip(page):
    """在剪辑条上点到 CLIP 附近，再用键盘把起点、终点收到 CLIP（Shift+方向键一次 1 秒）。"""
    strip = page.locator("#converter .strip")
    await page.wait_for_function("() => !document.querySelector('#converter .strip.loading')", timeout=30000)
    box = await strip.bounding_box()
    duration = await page.evaluate("() => document.querySelector('#converter video').duration")
    center = sum(CLIP) / 2
    await page.mouse.click(box["x"] + box["width"] * center / duration, box["y"] + box["height"] / 2)
    await page.wait_for_timeout(600)
    for label, target in (("起点", CLIP[0]), ("终点", CLIP[1])):
        handle = page.get_by_role("slider", name=label)
        await handle.focus()
        now = float(await handle.get_attribute("aria-valuenow"))
        key = "Shift+ArrowRight" if target > now else "Shift+ArrowLeft"
        for _ in range(round(abs(target - now))):
            await page.keyboard.press(key)
            await page.wait_for_timeout(70)
        await page.wait_for_timeout(400)


async def record(out: Path):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport=VIEWPORT)
        rec = Recorder(await page.context.new_cdp_session(page))
        await page.goto(SITE, wait_until="networkidle")
        await rec.start()
        await page.wait_for_timeout(1500)

        await page.click("#url")
        await page.keyboard.type(LINK, delay=45)
        await page.wait_for_timeout(500)
        await page.click("#parse-btn")
        await rec.waiting(page.wait_for_selector("#result:not([hidden])", timeout=120000))
        await page.wait_for_timeout(600)
        await scroll_to(page, "#result")
        await page.wait_for_timeout(2200)

        await page.get_by_role("button", name="转 GIF").click()
        generate = page.locator("#converter button:visible", has_text="生成 GIF")
        await rec.waiting(generate.wait_for(timeout=120000))
        await scroll_to(page, "#converter")
        await pick_clip(page)
        await page.wait_for_timeout(800)
        await generate.click()
        await rec.waiting(page.locator("#converter a:visible", has_text="保存 GIF").wait_for(timeout=120000))
        await page.wait_for_timeout(500)
        await scroll_to(page, "#converter .result", offset=260)
        await page.wait_for_timeout(3500)
        await rec.stop()
        end = time.time()
        await browser.close()

    out.mkdir(parents=True, exist_ok=True)
    frames = rec.timeline(end)
    lines = []
    for i, (img, seconds) in enumerate(frames):
        name = f"f{i:05d}.jpg"
        (out / name).write_bytes(img)
        lines += [f"file '{name}'", f"duration {seconds:.3f}"]
    lines.append(f"file 'f{len(frames) - 1:05d}.jpg'")  # concat 要求最后一帧再列一次
    (out / "frames.txt").write_text("\n".join(lines) + "\n")
    total = sum(s for _, s in frames)
    print(json.dumps({"frames": len(frames), "seconds": round(total, 1), "fast": len(rec.fast)}, ensure_ascii=False))


def encode(out: Path):
    scale = f"fps=15,scale={OUT_WIDTH}:-1:flags=lanczos"
    common = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(out / "frames.txt")]
    subprocess.run(
        [*common, "-vf", scale, "-c:v", "libwebp_anim", "-quality", "80", "-loop", "0", str(out / "demo.webp")],
        check=True,
    )
    # GIF 给不认 WebP 的地方用（投稿、论坛）：先按整段内容出调色板，只重绘变化的区域
    palette = (
        f"{scale},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
    )
    subprocess.run([*common, "-vf", palette, "-loop", "0", str(out / "demo.gif")], check=True)
    for name in ("demo.webp", "demo.gif"):
        print(name, (out / name).stat().st_size // 1024, "KB")


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/demo")
    asyncio.run(record(target))
    encode(target)
