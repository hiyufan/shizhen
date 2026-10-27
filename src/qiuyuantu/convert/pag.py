"""固定的官方 PAG WASM 在独立无外网浏览器中逐帧渲染。"""

from __future__ import annotations

import base64
from pathlib import Path

_ROOT = Path(__file__).parent
_ORIGIN = "https://animation.qiuyuantu.invalid"


def _resources(source: Path) -> dict[str, tuple[str, bytes]]:
    if source.stat().st_size > 8 << 20 or not source.read_bytes().startswith(b"PAG"):
        raise ValueError("PAG 文件无效或超过 8 MiB")
    return {
        "/": (
            "text/html",
            b'<!doctype html><canvas id="animation"></canvas>'
            b'<script type="module" src="/libpag.js"></script><script src="/render.js"></script>',
        ),
        "/libpag.js": ("text/javascript", (_ROOT / "vendor/libpag.min.js").read_bytes()),
        "/libpag.wasm": ("application/wasm", (_ROOT / "vendor/libpag.wasm").read_bytes()),
        "/render.js": ("text/javascript", (_ROOT.parent / "static/js/pag-render.js").read_bytes()),
        "/source.pag": ("application/octet-stream", source.read_bytes()),
    }


def render(source: Path, directory: Path) -> tuple[int, float]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("PAG 转换需要安装 qiuyuantu[animations] 和 Chromium") from exc
    resources = _resources(source)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ],
        )
        try:
            context = browser.new_context(service_workers="block", accept_downloads=False)

            def route(request):
                key = request.request.url.removeprefix(_ORIGIN)
                if request.request.url.startswith(_ORIGIN + "/") and key in resources:
                    content_type, content = resources[key]
                    request.fulfill(status=200, content_type=content_type, body=content)
                else:
                    request.abort()

            context.route("**/*", route)
            page = context.new_page()
            page.set_default_timeout(20_000)
            page.goto(_ORIGIN + "/")
            meta = page.evaluate("() => window.qytLoadPAG()")
            count, rate = int(meta["count"]), float(meta["rate"])
            for index in range(count):
                # 时间位置与动画的原始帧率对应，不按截图或等待耗时伪造动画帧。
                encoded = page.evaluate("(p) => window.qytPAGFrame(p)", index / count)
                (directory / f"frame{index:04d}.png").write_bytes(base64.b64decode(encoded, validate=True))
        except Exception as exc:
            message = str(exc)
            if "PAG 超过" in message:
                raise ValueError("PAG 超过尺寸、帧数或时长限制") from exc
            if "字体图层" in message:
                raise ValueError("这份 PAG 含字体图层，暂时只支持无字体动画") from exc
            raise ValueError("PAG 渲染失败，请检查文件或 Chromium 安装") from exc
        finally:
            browser.close()
    return count, rate
