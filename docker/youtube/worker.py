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


def _codec(f: dict) -> str:
    match = re.search(r'codecs="([^"]+)"', f.get("mimeType", ""))
    return match[1] if match else ""


def _short_side(f: dict) -> int:
    h, w = int(f.get("height") or 0), int(f.get("width") or 0)
    return min(h, w) if h and w else h


def _tier_ok(f: dict) -> bool:
    """1080p 以内只要 H.264，按短边算（竖屏视频高 1920 也是 1080p）；规则同应用里的 youtube_native.tiers。"""
    return _short_side(f) > 1080 or _codec(f).startswith("avc1")


def tiers(response: dict) -> list[list]:
    """[高度, 短边, 编码]：更高的只有 VP9 / AV1，优先 VP9，和 download 里的格式串一致。"""
    found: dict[int, list] = {}
    for f in response.get("streamingData", {}).get("adaptiveFormats", []):
        h = int(f.get("height") or 0)
        if not h or not f.get("mimeType", "").startswith("video/") or not _tier_ok(f):
            continue
        if h not in found or (_codec(f).startswith("vp9") and not found[h][2].startswith("vp9")):
            found[h] = [h, _short_side(f), _codec(f)]
    return sorted(found.values(), reverse=True)


def codec_filter(response: dict, height: int) -> str:
    """下载这一档时的编码条件：1080p 以内 H.264，更高的用 tiers 标出的编码（yt-dlp 默认会挑 AV1）。"""
    tier = next((t for t in tiers(response) if t[0] == height), None)
    if tier is None:
        return ""
    if tier[1] <= 1080:
        return "[vcodec^=avc1]"
    # 锁定标出的那一路：只写 vp 的话 yt-dlp 会优先挑 HDR 的 VP9.2
    return f"[vcodec={tier[2]}]" if re.fullmatch(r"[\w.]+", tier[2]) else ""


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
        if not h or not mime.startswith("video/") or not _tier_ok(f):
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


_MINT_JS = """async c => {
    const get = window.top['havuokmhhs-0']?.bevasrs?.wpc;
    if (!get) throw Error('WebPoClient unavailable');
    const client = await get();
    return await client.mws({c, mc:false, me:false});
}"""

_PLAYER_JS = """async body => {
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
}"""

_MEDIA_JS = """async p => {
    const r = await fetch(p.url, {method:p.method, headers:p.headers,
        body:p.body ? new Uint8Array(p.body) : undefined, credentials:'omit'});
    const b = new Uint8Array(await r.arrayBuffer()); let s='';
    for(let i=0;i<b.length;i+=8192)s+=String.fromCharCode(...b.subarray(i,i+8192));
    return {status:r.status,url:r.url,headers:Object.fromEntries(r.headers),body:btoa(s)};
}"""


class Session:
    """一个原生 Chromium 页面上的 YouTube 会话：预热、Token、player 请求和媒体请求都经它发出。"""

    def __init__(self, args, page, loop: asyncio.AbstractEventLoop) -> None:
        self.args = args
        self.page = page
        self.loop = loop
        self.compatibility = args.compatibility
        self.navigation = self.snapshot = self.player_js = None

    async def prepare(self, *, compatibility: bool) -> None:
        self.compatibility = compatibility
        self.navigation, self.snapshot, self.player_js = await prepare_session(
            self.page, self.args.video_id, activate=self.args.activate, compatibility=compatibility
        )

    async def open(self) -> None:
        """先走快速预热；页面没准备好就退回对照流程（固定等待）再来一次。"""
        try:
            await self.prepare(compatibility=self.compatibility)
        except Exception:
            if self.compatibility:
                raise
            report("compatibility_retry", reason="session_not_ready")
            await self.prepare(compatibility=True)

    async def mint(self, binding):
        for attempt in range(10):
            try:
                return await self.page.evaluate(_MINT_JS, binding)
            except Exception:
                if attempt == 9:
                    raise RuntimeError("原生 WebPoClient 未就绪") from None
                await asyncio.sleep(1)
        raise RuntimeError("原生 WebPoClient 未就绪")

    async def player(self, body):
        token = await self.mint(self.args.video_id)
        body["serviceIntegrityDimensions"] = {"poToken": token}
        # API 必须知道用于解码 URL 的播放器版本，否则元数据 OK 但媒体仍可能 403。
        playback = body.setdefault("playbackContext", {}).setdefault("contentPlaybackContext", {})
        playback["signatureTimestamp"] = self.player_js["sts"]
        return await self.page.evaluate(_PLAYER_JS, body)

    async def playable(self) -> dict:
        """目标视频的 player 响应；快速流程拿不到 OK 时按对照流程重新预热再请求一次。"""
        request = {"videoId": self.args.video_id, "contentCheckOk": True, "racyCheckOk": True}
        response = await self.player(dict(request))
        if response.get("playabilityStatus", {}).get("status") != "OK" and not self.compatibility:
            report("compatibility_retry", reason="player_not_ready")
            await self.prepare(compatibility=True)
            response = await self.player(dict(request))
        return response

    async def media(self, req):
        headers = {k: v for k, v in req.headers.items() if k.lower() in ("content-type", "accept", "range")}
        payload = {"url": req.url, "method": req.method, "body": list(req.data) if req.data else None}
        return await self.page.evaluate(_MEDIA_JS, payload | {"headers": headers})

    def wait(self, coroutine, seconds: float):
        """yt-dlp 跑在线程里，回到页面所在的事件循环执行浏览器调用。"""
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(seconds)


def check_player(response: dict, video_id: str) -> tuple[dict, float]:
    """(videoDetails, 时长)；不是目标视频、不可播放或没有完整时长都直接失败。"""
    details, state = response.get("videoDetails", {}), response.get("playabilityStatus", {})
    report(
        "player",
        id=details.get("videoId"),
        status=state.get("status"),
        reason=redact(state.get("reason", "")),
        formats=len(response.get("streamingData", {}).get("adaptiveFormats", [])),
    )
    if details.get("videoId") != video_id or state.get("status") != "OK":
        raise RuntimeError("目标视频 player 请求失败")
    duration = float(details.get("lengthSeconds") or 0)
    if duration <= 0:
        raise RuntimeError("本实验仅验收有完整时长的非直播视频")
    return details, duration


def watch_html(config: dict, response: dict, initial: dict, raw_html: str) -> str:
    """交给 yt-dlp 的观看页：前面补上浏览器里实际拿到的配置和 player 响应。"""
    return (
        "<html><script>ytcfg.set("
        + json.dumps(config)
        + ");var ytInitialPlayerResponse = "
        + json.dumps(response)
        + ";var ytInitialData = "
        + json.dumps(initial)
        + ";</script></html>"
        + raw_html
    )


def cookie_lines(cookies: list[dict]) -> list[str]:
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        fields = [
            c["domain"],
            "TRUE" if c["domain"].startswith(".") else "FALSE",
            c["path"],
            "TRUE" if c["secure"] else "FALSE",
            str(max(0, int(c["expires"]))),
            c["name"],
            c["value"],
        ]
        lines.append("\t".join(fields))
    return lines


def content_bound(config: dict) -> bool:
    """播放器要求按视频 ID 生成 GVS Token 时，Token 绑定视频而不是访客。"""
    return any(
        "true" in parse_qs(v.get("serializedExperimentFlags", "")).get("html5_generate_content_po_token", [])
        for v in config.get("WEB_PLAYER_CONTEXT_CONFIGS", {}).values()
        if isinstance(v, dict)
    )


def extractor_class(session: Session, html: str, binding_visitor: str, bound: bool) -> type:
    args = session.args

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
                result = session.wait(session.player(json.loads(kw.get("data") or b"{}")), 30)
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
            binding = binding_visitor if kind == "gvs" and not bound else args.video_id
            if not binding:
                raise RuntimeError("缺少 Token 内容绑定")
            return session.wait(session.mint(binding), 20)

    return BrowserYoutubeIE


def downloader_class(session: Session) -> type:
    class BrowserDownloader(YoutubeDL):
        def urlopen(self, req):
            if not (
                session.args.transport == "browser"
                and hasattr(req, "url")
                and (urlparse(req.url).hostname or "").endswith(".googlevideo.com")
            ):
                return super().urlopen(req)
            reply = session.wait(session.media(req), 30)
            result = Response(
                io.BytesIO(base64.b64decode(reply["body"])), reply["url"], reply["headers"], status=reply["status"]
            )
            if reply["status"] >= 400:
                raise HTTPError(result)
            return result

    return BrowserDownloader


def ytdlp_options(args, response: dict, height: int, cookiefile: Path, user_agent: str, work: Path) -> dict:
    # 1080p 以内 H.264；更高分辨率通常只有 VP9 / AV1，不能悄悄降到 1080p。
    codec = codec_filter(response, height)
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
        "http_headers": {"User-Agent": user_agent},
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
    return opts


def public_result(video_id: str, details: dict, response: dict) -> dict:
    thumbnails = details.get("thumbnail", {}).get("thumbnails") or []
    return {
        "id": video_id,
        "title": details.get("title") or "YouTube",
        "author": details.get("author") or "",
        "channel_id": details.get("channelId") or "",
        "thumbnail": thumbnails[-1].get("url", "") if thumbnails else "",
        "tiers": tiers(response),
    }


async def download(session: Session, context, profile: str, work: Path) -> tuple[dict, float]:
    """在已预热的会话上拿 player 响应、选档并用 SABR 下完，返回 (公开元数据, 时长)。"""
    args = session.args
    response = await session.playable()
    details, duration = check_player(response, args.video_id)
    height, expected = plan(response, args.height, args.max_bytes, duration, fit=args.fit)
    report("plan", height=height, bytes=expected, duration=duration)
    snapshot = session.snapshot
    snapshot["response"] = response
    config = snapshot["config"]
    navigation = session.navigation
    raw_html = await navigation.text() if navigation else await session.page.content()
    html = watch_html(config, response, snapshot["initial"], raw_html)
    cookiefile = Path(profile) / "guest.txt"
    cookiefile.write_text("\n".join(cookie_lines(await context.cookies())) + "\n")
    cookiefile.chmod(0o600)
    visitor = config.get("VISITOR_DATA") or config["INNERTUBE_CONTEXT"]["client"].get("visitorData")
    extractor = extractor_class(session, html, visitor, content_bound(config))
    opts = ytdlp_options(args, response, height, cookiefile, snapshot["ua"], work)

    def run_ytdlp():
        report("download_start")
        with downloader_class(session)(opts) as ydl:
            ydl.add_info_extractor(extractor(ydl))
            info = ydl.extract_info("https://www.youtube.com/watch?v=" + args.video_id, download=True, ie_key="Youtube")
            if info["id"] != args.video_id:
                raise RuntimeError("下载器返回了其它视频")

    await asyncio.to_thread(run_ytdlp)
    return public_result(args.video_id, details, response), duration


def chromium_command(executable: str, port: int, profile: str, proxy: str) -> list[str]:
    command = [
        executable,
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        f"--remote-debugging-port={port}",
        "--user-data-dir=" + profile,
        "about:blank",
    ]
    if proxy:
        command.insert(1, "--proxy-server=" + proxy)
    return command


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def connect(pw, port: int):
    for _ in range(40):
        try:
            return await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        except Exception:
            await asyncio.sleep(0.25)
    raise RuntimeError("Chromium 未就绪")


async def run(args) -> dict:
    # 不把 cookies、Token、媒体签名 URL 或完整 player JSON 写入输出目录。
    work = args.output / f"{args.video_id}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    async with async_playwright() as pw:
        with (
            display(report_resources=args.report_resources) as screen,
            tempfile.TemporaryDirectory(prefix="youtube-probe-", ignore_cleanup_errors=True) as profile,
        ):
            port = free_port()
            proc = subprocess.Popen(
                chromium_command(pw.chromium.executable_path, port, profile, args.proxy),
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
                browser = await connect(pw, port)
                report("chromium_ready")
                context = browser.contexts[0]
                session = Session(args, await context.new_page(), asyncio.get_running_loop())
                await session.open()
                result, duration = await download(session, context, profile, work)
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
    parser.add_argument("--height", type=int, default=720)
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
    if not 16 <= args.height <= 16384:
        parser.error("视频高度必须在 16 到 16384 像素之间")
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
