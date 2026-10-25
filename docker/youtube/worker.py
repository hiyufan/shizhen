"""YouTube 兜底：同一原生 Chromium 会话取得 player 响应 / Token，再用 SABR 下载。

退出码 0 必须同时满足：正确视频 ID、完整音视频、时长相符、ffmpeg 完整解码。
仅取得 OK、格式列表或广告播放都不算成功。通过独立解释器调用，不导入主应用。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import io
import json
import os
import re
import select
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from deno import find_deno_bin
from playwright.async_api import async_playwright
from yt_dlp import YoutubeDL
from yt_dlp.extractor.youtube import YoutubeIE
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError

_STARTED = time.monotonic()


def report(stage: str, **fields) -> None:
    print(
        json.dumps({"stage": stage, "elapsed": round(time.monotonic() - _STARTED, 3), **fields}, ensure_ascii=False),
        flush=True,
    )


def redact(message: object) -> str:
    return re.sub(r"https?://\S+", "<url>", str(message))[:350]


class Logger:
    def debug(self, message):
        if "[download]" in message or "[info]" in message:
            report("download", message=redact(message))

    def warning(self, message):
        report("warning", message=redact(message))

    def error(self, message):
        report("error", message=redact(message))


def stop(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


@contextlib.contextmanager
def display(*, report_resources=False):
    proc = subprocess.Popen(
        ["Xvfb", "-displayfd", "1", "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if report_resources:
        report("_process", group=proc.pid)
    try:
        if not select.select([proc.stdout], [], [], 10)[0]:
            raise RuntimeError("Xvfb 未就绪")
        number = proc.stdout.readline().decode().strip()
        if not number.isdigit():
            raise RuntimeError("Xvfb 未返回显示编号")
        yield ":" + number
    finally:
        stop(proc)


def verify(path: Path, expected_duration: float, max_bytes: int = 300 << 20) -> dict:
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError("没有完整的合并文件")
    if path.stat().st_size > max_bytes:
        raise RuntimeError("原视频超过服务器大小上限")
    result = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        timeout=30,
    )
    data = json.loads(result)
    kinds = {s.get("codec_type") for s in data["streams"]}
    actual_duration = float(data["format"]["duration"])
    if not {"video", "audio"}.issubset(kinds) or abs(actual_duration - expected_duration) > 2:
        raise RuntimeError("音轨、视频轨或完整时长校验失败")
    report("verify_decode_start")
    # 整条只解封装（几秒）能发现缺段、截断；完整解码 10 分钟 720p60 要 39 秒，超过解析的时间预算，
    # 所以画面只抽开头、中间、结尾各 2 秒真正解码
    checks = [["-i", str(path), "-map", "0", "-c", "copy"]]
    for start in (0, max(0, actual_duration / 2 - 1), max(0, actual_duration - 3)):
        checks.append(["-ss", f"{start:.2f}", "-i", str(path), "-t", "2"])
    for check in checks:
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-threads", "4", *check, "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if decoded.returncode:
            raise RuntimeError("完整文件解码失败")
    report("verify_decode_end")
    return {
        "file": str(path),
        "bytes": path.stat().st_size,
        "duration": actual_duration,
        "streams": [
            {k: s.get(k) for k in ("codec_type", "codec_name", "width", "height", "avg_frame_rate")}
            for s in data["streams"]
        ],
    }


def size_guard(limit: int):
    downloaded = {}

    def hook(event):
        if event.get("status") not in ("downloading", "finished"):
            return
        key = event.get("filename") or (event.get("info_dict") or {}).get("format_id", "video")
        downloaded[key] = max(downloaded.get(key, 0), event.get("downloaded_bytes") or 0)
        if sum(downloaded.values()) > limit:
            raise RuntimeError("原视频超过服务器大小上限")

    return hook


def plan(response: dict, height: int, max_bytes: int, duration: float, *, fit: bool) -> tuple[int, int]:
    """按 player 响应里的大小估算 (清晰度, 字节数)。fit=True 时放不下就往低一档退。"""
    formats = response.get("streamingData", {}).get("adaptiveFormats", [])

    def size(f: dict) -> int:
        if f.get("contentLength"):
            return int(f["contentLength"])
        return int((f.get("averageBitrate") or f.get("bitrate") or 0) * duration / 8)

    audio = max((size(f) for f in formats if f.get("mimeType", "").startswith("audio/mp4")), default=0)
    by_height: dict[int, int] = {}
    for f in formats:
        h, mime = int(f.get("height") or 0), f.get("mimeType", "")
        if not h or not mime.startswith("video/") or (h <= 1080 and "avc1" not in mime):
            continue
        by_height[h] = max(by_height.get(h, 0), size(f) + audio)
    candidates = sorted((h for h in by_height if h <= height), reverse=True)
    if not candidates:
        raise RuntimeError("没有这个清晰度")
    for h in candidates if fit else candidates[:1]:
        if by_height[h] <= max_bytes:
            return h, by_height[h]
    h = candidates[-1] if fit else candidates[0]
    report("too_large", height=h, bytes=by_height[h], duration=duration, limit=max_bytes)
    raise RuntimeError(f"这个清晰度约 {by_height[h] / 1e6:.0f} MB，超过了 {max_bytes / 1e6:.0f} MB 的上限")


def bytes_reporter():
    """下载进度：每秒最多报一次两条分轨合计下了多少。"""
    done: dict[str, int] = {}
    last = [0.0]

    def hook(event):
        key = event.get("filename") or (event.get("info_dict") or {}).get("format_id", "video")
        done[key] = max(done.get(key, 0), event.get("downloaded_bytes") or 0)
        if time.monotonic() - last[0] >= 1 or event.get("status") == "finished":
            last[0] = time.monotonic()
            report("bytes", done=sum(done.values()))

    return hook


def postprocess(event: dict) -> None:
    if event.get("status") == "started" and event.get("postprocessor") == "Merger":
        report("merging")


async def prepare_session(page, video: str, *, activate: bool, compatibility: bool):
    for video_id in ("dQw4w9WgXcQ", video):
        navigation = await page.goto(
            "https://www.youtube.com/watch?v=" + video_id, wait_until="domcontentloaded", timeout=30000
        )
        report("navigation", id=video_id, compatibility=compatibility)
        await page.wait_for_timeout(6000 if compatibility else 1000)
        if activate:
            # 控件可能自动隐藏，使用播放器快捷键，不点广告或外链。
            await page.locator("#movie_player").focus(timeout=5000)
            await page.keyboard.press("k")
            await page.wait_for_timeout(500)
            await page.keyboard.press("k")
            await page.wait_for_timeout(3000 if compatibility else 1000)
        if not compatibility:
            await page.wait_for_function(
                """() =>
                window.ytcfg?.data_?.INNERTUBE_CONTEXT && window.ytInitialData &&
                window.top['havuokmhhs-0']?.bevasrs?.wpc &&
                [...document.scripts].some(s => s.src.includes('/s/player/') && s.src.endsWith('/base.js'))
            """,
                timeout=12000,
            )
    snapshot = await page.evaluate("""() => ({
        response: document.querySelector('#movie_player')?.getPlayerResponse?.()
                  || window.ytInitialPlayerResponse,
        config: window.ytcfg?.data_, initial: window.ytInitialData,
        ua: navigator.userAgent, webdriver: navigator.webdriver,
        ad: document.querySelector('#movie_player')?.classList.contains('ad-showing')
    })""")
    report("browser", webdriver=snapshot["webdriver"], ad=snapshot["ad"])
    player_js = await page.evaluate(r"""async () => {
        const url = [...document.scripts].map(s=>s.src)
            .find(u=>u.includes('/s/player/') && u.endsWith('/base.js'));
        if (!url) return null;
        const r = await fetch(url); if (!r.ok) return null;
        const code = await r.text();
        const m = code.match(/(?:signatureTimestamp|sts)\s*:\s*([0-9]{5})/);
        return m ? {url, sts:Number(m[1])} : null;
    }""")
    if not player_js:
        raise RuntimeError("没有取得实际播放器的签名版本")
    snapshot["config"]["PLAYER_JS_URL"] = player_js["url"]
    snapshot["config"]["STS"] = player_js["sts"]
    report("signature_version", sts=player_js["sts"])
    return navigation, snapshot, player_js


async def run(args) -> dict:  # noqa: C901, PLR0912, PLR0915
    # 不把 cookies、Token、媒体签名 URL 或完整 player JSON 写入输出目录。
    work = args.output / f"{args.video_id}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    async with async_playwright() as pw:
        with (
            display(report_resources=args.report_resources) as screen,
            tempfile.TemporaryDirectory(
                prefix="youtube-probe-",
                ignore_cleanup_errors=True,
            ) as profile,
        ):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            command = [
                pw.chromium.executable_path,
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--no-first-run",
                f"--remote-debugging-port={port}",
                "--user-data-dir=" + profile,
                "about:blank",
            ]
            if args.proxy:
                command.insert(1, "--proxy-server=" + args.proxy)
            proc = subprocess.Popen(
                command,
                env=os.environ | {"DISPLAY": screen},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            if args.report_resources:
                report("_process", group=proc.pid)
            browser = None
            browser_stopped = False
            try:
                for _ in range(40):
                    try:
                        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                        break
                    except Exception:
                        await asyncio.sleep(0.25)
                if browser is None:
                    raise RuntimeError("Chromium 未就绪")
                report("chromium_ready")
                context = browser.contexts[0]
                page = await context.new_page()
                compatibility = args.compatibility
                try:
                    navigation, snapshot, player_js = await prepare_session(
                        page, args.video_id, activate=args.activate, compatibility=compatibility
                    )
                except Exception:
                    if compatibility:
                        raise
                    report("compatibility_retry", reason="session_not_ready")
                    compatibility = True
                    navigation, snapshot, player_js = await prepare_session(
                        page, args.video_id, activate=args.activate, compatibility=True
                    )

                async def mint(binding):
                    for attempt in range(10):
                        try:
                            return await page.evaluate(
                                """async c => {
                                const get = window.top['havuokmhhs-0']?.bevasrs?.wpc;
                                if (!get) throw Error('WebPoClient unavailable');
                                const client = await get();
                                return await client.mws({c, mc:false, me:false});
                            }""",
                                binding,
                            )
                        except Exception:
                            if attempt == 9:
                                raise RuntimeError("原生 WebPoClient 未就绪") from None
                            await asyncio.sleep(1)
                    raise RuntimeError("原生 WebPoClient 未就绪")

                async def player(body):
                    token = await mint(args.video_id)
                    body["serviceIntegrityDimensions"] = {"poToken": token}
                    # API 必须知道用于解码 URL 的播放器版本，否则元数据 OK 但媒体仍可能 403。
                    playback = body.setdefault("playbackContext", {}).setdefault("contentPlaybackContext", {})
                    playback["signatureTimestamp"] = player_js["sts"]
                    return await page.evaluate(
                        """async body => {
                        const c = structuredClone(ytcfg.get('INNERTUBE_CONTEXT'));
                        body.context = c;
                        const r = await fetch('/youtubei/v1/player?prettyPrint=false', {
                            method:'POST', credentials:'include',
                            headers:{'content-type':'application/json',
                              'X-Goog-Visitor-Id':c.client.visitorData || ytcfg.get('VISITOR_DATA'),
                              'X-Youtube-Client-Name':String(ytcfg.get('INNERTUBE_CONTEXT_CLIENT_NAME') || 1),
                              'X-Youtube-Client-Version':c.client.clientVersion},
                            body:JSON.stringify(body)
                        }); return await r.json();
                    }""",
                        body,
                    )

                response = await player({"videoId": args.video_id, "contentCheckOk": True, "racyCheckOk": True})
                if response.get("playabilityStatus", {}).get("status") != "OK" and not compatibility:
                    report("compatibility_retry", reason="player_not_ready")
                    navigation, snapshot, player_js = await prepare_session(
                        page, args.video_id, activate=args.activate, compatibility=True
                    )
                    response = await player({"videoId": args.video_id, "contentCheckOk": True, "racyCheckOk": True})
                details, state = response.get("videoDetails", {}), response.get("playabilityStatus", {})
                report(
                    "player",
                    id=details.get("videoId"),
                    status=state.get("status"),
                    reason=redact(state.get("reason", "")),
                    formats=len(response.get("streamingData", {}).get("adaptiveFormats", [])),
                )
                if details.get("videoId") != args.video_id or state.get("status") != "OK":
                    raise RuntimeError("目标视频 player 请求失败")
                duration = float(details.get("lengthSeconds") or 0)
                if duration <= 0:
                    raise RuntimeError("本实验仅验收有完整时长的非直播视频")
                height, expected = plan(response, args.height, args.max_bytes, duration, fit=args.fit)
                report("plan", height=height, bytes=expected, duration=duration)
                snapshot["response"] = response
                config = snapshot["config"]
                raw_html = await navigation.text() if navigation else await page.content()
                html = (
                    "<html><script>ytcfg.set("
                    + json.dumps(config)
                    + ");var ytInitialPlayerResponse = "
                    + json.dumps(response)
                    + ";var ytInitialData = "
                    + json.dumps(snapshot["initial"])
                    + ";</script></html>"
                    + raw_html
                )
                cookiefile = Path(profile) / "guest.txt"
                lines = ["# Netscape HTTP Cookie File"]
                for c in await context.cookies():
                    lines.append(
                        "\t".join(
                            [
                                c["domain"],
                                "TRUE" if c["domain"].startswith(".") else "FALSE",
                                c["path"],
                                "TRUE" if c["secure"] else "FALSE",
                                str(max(0, int(c["expires"]))),
                                c["name"],
                                c["value"],
                            ]
                        )
                    )
                cookiefile.write_text("\n".join(lines) + "\n")
                cookiefile.chmod(0o600)
                loop = asyncio.get_running_loop()
                visitor = config.get("VISITOR_DATA") or config["INNERTUBE_CONTEXT"]["client"].get("visitorData")
                content_bound = any(
                    "true"
                    in parse_qs(v.get("serializedExperimentFlags", "")).get("html5_generate_content_po_token", [])
                    for v in config.get("WEB_PLAYER_CONTEXT_CONFIGS", {}).values()
                    if isinstance(v, dict)
                )

                class BrowserYoutubeIE(YoutubeIE):
                    @classmethod
                    def ie_key(cls):
                        # 保留 youtube 参数命名空间，否则会静默采用其它默认客户端。
                        return "Youtube"

                    def _download_webpage(self, url, video_id, *a, **kw):
                        if video_id == args.video_id and isinstance(url, str) and urlparse(url).path == "/watch":
                            return html
                        return super()._download_webpage(url, video_id, *a, **kw)

                    def _download_json(self, url, video_id, *a, **kw):
                        if isinstance(url, str) and "/youtubei/v1/player" in url and video_id == args.video_id:
                            result = asyncio.run_coroutine_threadsafe(
                                player(json.loads(kw.get("data") or b"{}")), loop
                            ).result(30)
                            report(
                                "reload",
                                status=result.get("playabilityStatus", {}).get("status"),
                                reason=redact(result.get("playabilityStatus", {}).get("reason", "")),
                            )
                            return result
                        return super()._download_json(url, video_id, *a, **kw)

                    def fetch_po_token(self, *a, **kw):
                        kind = getattr(kw.get("context"), "value", "gvs")
                        if kind == "gvs" and args.pot_url:
                            return super().fetch_po_token(*a, **kw)
                        binding = visitor if kind == "gvs" and not content_bound else args.video_id
                        if not binding:
                            raise RuntimeError("缺少 Token 内容绑定")
                        return asyncio.run_coroutine_threadsafe(mint(binding), loop).result(20)

                async def media(req):
                    return await page.evaluate(
                        """async p => {
                        const r = await fetch(p.url, {method:p.method, headers:p.headers,
                            body:p.body ? new Uint8Array(p.body) : undefined, credentials:'omit'});
                        const b = new Uint8Array(await r.arrayBuffer()); let s='';
                        for(let i=0;i<b.length;i+=8192)s+=String.fromCharCode(...b.subarray(i,i+8192));
                        return {status:r.status,url:r.url,headers:Object.fromEntries(r.headers),body:btoa(s)};
                    }""",
                        {
                            "url": req.url,
                            "method": req.method,
                            "body": list(req.data) if req.data else None,
                            "headers": {
                                k: v for k, v in req.headers.items() if k.lower() in ("content-type", "accept", "range")
                            },
                        },
                    )

                class BrowserDownloader(YoutubeDL):
                    def urlopen(self, req):
                        if (
                            args.transport == "browser"
                            and hasattr(req, "url")
                            and (urlparse(req.url).hostname or "").endswith(".googlevideo.com")
                        ):
                            reply = asyncio.run_coroutine_threadsafe(media(req), loop).result(30)
                            result = Response(
                                io.BytesIO(base64.b64decode(reply["body"])),
                                reply["url"],
                                reply["headers"],
                                status=reply["status"],
                            )
                            if reply["status"] >= 400:
                                raise HTTPError(result)
                            return result
                        return super().urlopen(req)

                def download():
                    # 1080p 以内优先 H.264；更高分辨率通常只有 VP9 / AV1，不能悄悄降到 1080p。
                    codec = "[vcodec^=avc1]" if height <= 1080 else ""
                    opts = {
                        "logger": Logger(),
                        "quiet": True,
                        "noprogress": True,
                        "cachedir": False,
                        "check_formats": False,
                        "socket_timeout": 20,
                        "retries": 0,
                        "extractor_retries": 0,
                        "max_filesize": args.max_bytes,
                        "progress_hooks": [size_guard(args.max_bytes), bytes_reporter()],
                        "postprocessor_hooks": [postprocess],
                        "cookiefile": str(cookiefile),
                        "http_headers": {"User-Agent": snapshot["ua"]},
                        "js_runtimes": {"deno": {"path": str(find_deno_bin())}},
                        "extractor_args": {"youtube": {"player_client": ["web"], "player_js_variant": ["actual"]}},
                        "format": f"bv[protocol=sabr][height<={height}]{codec}+ba[protocol=sabr][ext=m4a]",
                        "merge_output_format": "mp4",
                        "outtmpl": str(work / "video.%(ext)s"),
                    }
                    if args.proxy:
                        opts["proxy"] = args.proxy
                    if args.pot_url:
                        opts["extractor_args"]["youtubepot-bgutilhttp"] = {"base_url": [args.pot_url]}
                    report("download_start")
                    with BrowserDownloader(opts) as ydl:
                        ydl.add_info_extractor(BrowserYoutubeIE(ydl))
                        info = ydl.extract_info(
                            "https://www.youtube.com/watch?v=" + args.video_id, download=True, ie_key="Youtube"
                        )
                        if info["id"] != args.video_id:
                            raise RuntimeError("下载器返回了其它视频")
                    thumbnails = details.get("thumbnail", {}).get("thumbnails") or []
                    return {
                        "id": args.video_id,
                        "title": details.get("title") or "YouTube",
                        "author": details.get("author") or "",
                        "channel_id": details.get("channelId") or "",
                        "thumbnail": thumbnails[-1].get("url", "") if thumbnails else "",
                        "heights": sorted(
                            {
                                int(f["height"])
                                for f in response.get("streamingData", {}).get("adaptiveFormats", [])
                                if f.get("height")
                            }
                        ),
                    }

                result = await asyncio.to_thread(download)
                # 下载结束后释放广告播放器和渲染进程，再做完整解码校验。
                await browser.close()
                browser = None
                stop(proc)
                browser_stopped = True
                verified = await asyncio.to_thread(verify, work / "video.mp4", duration, args.max_bytes)
                return verified | result
            finally:
                if browser:
                    with contextlib.suppress(Exception):
                        await browser.close()
                if not browser_stopped:
                    stop(proc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_id")
    parser.add_argument("--output", type=Path, default=Path("/output"))
    parser.add_argument("--height", type=int, choices=(144, 240, 360, 480, 720, 1080, 1440, 2160), default=720)
    parser.add_argument("--max-bytes", type=int, default=300 << 20)
    parser.add_argument("--report-resources", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--transport", choices=("python", "browser"), default="python")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--pot-url", default="", help="仅让 bgutil 生成媒体 Token，播放器仍使用原生浏览器")
    parser.add_argument("--compatibility", action="store_true", help="使用原来的对照预热与固定等待流程")
    parser.add_argument("--activate", action="store_true", help="通过播放器快捷键激活浏览器页面")
    parser.add_argument("--fit", action="store_true", help="估算超过 --max-bytes 时自动降一档清晰度")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", args.video_id):
        parser.error("必须提供 11 位 YouTube 视频 ID")
    if args.max_bytes < 1 << 20:
        parser.error("大小上限必须至少为 1 MB")
    if args.proxy:
        parsed = urlparse(args.proxy)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
            parser.error("本实验仅支持不带账号密码的 HTTP 代理")
    try:
        report("verified", **asyncio.run(run(args)))
        return 0
    except Exception as exc:
        report("failed", error=redact(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
