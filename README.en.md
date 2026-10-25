<div align="center">

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/logo.svg">
  <img src="docs/logo-animated.gif" width="72" height="72" alt="Qiuyuantu">
</picture>

# Qiuyuantu · 求原图

[简体中文](README.md) · [English](README.en.md)

**Paste a link to extract watermark-free videos and original images, then turn them into GIFs, iPhone Live Photos, or Android Motion Photos.**

Supports 30+ platforms, including Douyin, Xiaohongshu, Kuaishou, YouTube, X, and Bilibili. Free to self-host. No sign-in required to use the app.

<sub>Formerly called Shizhen (拾帧), renamed Qiuyuantu (求原图) in October 2026. The repository URL remains unchanged.</sub>

[![License](https://img.shields.io/badge/license-MIT-1a1815.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-6BAFDF.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-4DB899.svg)](https://fastapi.tiangolo.com/)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-auto--update-E8702A.svg)](https://github.com/yt-dlp/yt-dlp)
[![Docker](https://img.shields.io/badge/docker-compose%20ready-1a1815.svg)](docker-compose.yml)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-6BAFDF.svg)](#contributing)

### [🌐 Try it at ynvan.com](https://ynvan.com)

[Quick start](#quick-start) · [Features](#features) · [Deployment](#production-deployment) · [Performance](#performance) · [Configuration](#configuration) · [API](#api) · [Architecture](#architecture) · [Security](#security) · [Contributing](#contributing)

</div>

<br>

<p align="center">
  <img src="docs/demo.webp" width="100%" alt="Demo: extract a YouTube video, then turn a four-second clip into a GIF">
</p>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshot-convert.png" alt="Conversion panel: select a clip on the thumbnail strip and create a Live Photo"></td>
    <td width="50%"><img src="docs/screenshot-guide.png" alt="Tutorial page"></td>
  </tr>
  <tr>
    <td align="center"><sub>Turn a clip from any video into a Live Photo and choose its cover frame</sub></td>
    <td align="center"><sub>Each tutorial answers a specific question</sub></td>
  </tr>
</table>

---

## Why this project exists

Short-video platforms keep content inside their apps: saved videos have watermarks, images are compressed, and Live Photos become a single still frame. Making a GIF means finding yet another tool. Qiuyuantu brings these steps together:

| Step | What it does |
|---|---|
| Extract | Paste a share link to get the watermark-free playback URLs and original-size images the platform serves to web clients |
| Convert | Select a few seconds on the thumbnail strip and generate a GIF, Live Photo, or Motion Photo |
| Package originals | Pair the original image and short video from a Xiaohongshu or Douyin Live Photo so it animates when imported into your photo library |

Extraction builds on [parse-video-py](https://github.com/wujunwei928/parse-video-py) and [yt-dlp](https://github.com/yt-dlp/yt-dlp). This project combines them, adds media conversion, and provides a website suitable for public deployment.

## Features

<table>
<tr><td width="33%" valign="top">

**Extraction**

- Original watermark-free video streams
- Original Xiaohongshu images, commonly 1920×2560
- Douyin HD H.265 video and separate background music
- Server-side merging for YouTube / Bilibili HD formats, up to 4K where available
- Live Photo detection and packaging for an entire post
- Media proxy for browser playback when direct requests return 403
- Errors classified as deleted content, login required, rate limited, and more

</td><td width="33%" valign="top">

**Conversion**

- **GIF**: up to 30 seconds, 5–30 fps, 160–960 px wide, 0.5–3× speed, two-pass palette encoding
- **iPhone Live Photo**: up to 10 seconds, paired JPG + MOV, with `ContentIdentifier` written to both files and verified by reading it back
- **Android Motion Photo**: JPG with an embedded MP4, Motion Photo + MicroVideo XMP metadata
- **Original packaging**: remux platform Live Photos without re-encoding
- **Local videos**: upload and convert files up to 300 MB

</td><td width="33%" valign="top">

**Website**

- Minimal editorial design with full mobile support
- Self-hosted fonts; no external requests for page assets
- 8 landing pages and 13 tutorials with JSON-LD structured data
- Public deployment protections: rate limits, quotas, signatures, SSRF prevention, CSP
- Docker runs as a non-root user
- Automatic yt-dlp updates to keep up with platform changes
- Admin statistics at `/stats`: hourly / daily usage, platform success rates, and failure reasons

</td></tr>
</table>

## Supported platforms

| Platform | Video | Image galleries | Live Photos | Quality | Notes |
|---|:-:|:-:|:-:|---|---|
| Douyin | ✅ | ✅ | ✅ | 720p–1080p, H.265 | Background music can be downloaded separately |
| Xiaohongshu (RedNote) | ✅ | ✅ | ✅ | Highest available | Links need a valid `xsec_token`; copy a fresh share link before use |
| Kuaishou | ✅ | ✅ | – | Platform default | |
| YouTube | ✅ | – | – | Readable direct URLs; server-side merging at 720p–4K | Docker includes a native session / SABR fallback |
| X (Twitter) | ✅ | ✅ | – | Highest bitrate | fxtwitter fallback for sensitive posts |
| Bilibili | ✅ | ✅ | ✅ | 480p direct URLs; server-side merging at 720p / 1080p | Supports b23.tv short links, images and Live Photos in dynamic posts; anime series and movies are not supported |
| Instagram | ✅ | ✅ | – | Platform default | Original images from carousel posts, including posts mixing images and videos |
| Weibo, Xigua, Pipixia, AcFun, TikTok, … | ✅ | Partial | – | | 30+ platforms, with yt-dlp as the fallback for others |

> Extraction depends on each platform's web structure and may temporarily break when it changes. Upstream parse-video-py is updated frequently. Port relevant fixes into `parser/` by comparing changes; do not overwrite local parsers wholesale. See [Contributing](#contributing).

## Quick start

Requires **Python 3.10+**. You do not need to install FFmpeg separately: the app uses the version on `PATH`, or the binary bundled with `imageio-ffmpeg` if none is found.

```bash
git clone https://github.com/hiyufan/shizhen.git && cd shizhen
./start.sh          # Windows: .\start.ps1
```

The first run creates `.venv` and installs dependencies automatically, then opens <http://127.0.0.1:8000>.

The Python package, distribution, and CLI are all named `qiuyuantu`, for example `from qiuyuantu import parse_video_share_url` and `qiuyuantu version`. CLI features require the `cli` extra. Legacy `parse_video_py` imports, including submodules, the `parse-video-py` command, and `python -m parse_video_py.diag` remain compatible. HTTP endpoints and `PARSE_VIDEO_*` environment variables keep their existing names and behavior.

When upgrading an older source installation, first run `python -m pip uninstall parse-video-py` in its virtual environment, then install this repository using the commands below. This avoids having two distributions provide the legacy entry points. For Docker installations, rebuild the image. The repository remains at `hiyufan/shizhen`.

<details>
<summary>Manual installation / single Docker container</summary>

```bash
# Manual installation
python -m venv .venv
.venv/bin/pip install -e ".[web,cli]"      # Windows: .venv\Scripts\pip
.venv/bin/python main.py

# Docker
docker build -t shizhen .
docker run -d -p 8000:8000 -v ./data:/app/data shizhen
```

</details>

## Production deployment

A VPS with **4 CPU cores / 8 GB RAM / 100 Mbps** is sufficient. `docker-compose.yml` includes four services: the app, [Caddy](https://caddyserver.com/) for reverse proxying with automatic HTTPS, [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) for YouTube PO Tokens, and WARP as a backup outbound connection.

```bash
# Point your domain's DNS to this server first
DOMAIN=your.domain PARSE_VIDEO_SITE_URL=https://your.domain docker compose up -d
```

Without Docker, configure Nginx / Caddy to reverse proxy to `127.0.0.1:8000` and set `PARSE_VIDEO_TRUST_PROXY=1` so rate limiting uses the real client IP.

For diagnostics, run `docker compose exec app python -m qiuyuantu.diag "<share-link>"`. It prints the outbound IP, extraction result, and raw status returned by the platform.

Pushes to `main` deploy automatically after CI passes, with an automatic rollback if deployment checks fail. Setup instructions: [docs/deploy.md](docs/deploy.md) (Chinese).

<details>
<summary><b>Hosting outside mainland China</b>: Xiaohongshu / Bilibili may reject overseas and datacenter IPs</summary>

<br>

Bilibili's API may return 412, while Xiaohongshu may return a verification page. There are four options:

**1. Give Chinese platforms a dedicated mainland China outbound connection** (recommended)

```bash
PARSE_VIDEO_PROXY_CN=http://user:pass@host:port
```

YouTube and other platforms still connect directly. Only extraction requests, which use little bandwidth, go through this proxy. By default, the server fetches videos directly from the CDN; enable `PARSE_VIDEO_PROXY_CN_MEDIA=1` if needed. Possible outbound connections:

| Option | Cost | Notes |
|---|---|---|
| Home PC / NAS / Raspberry Pi + Tailscale | Free | Residential IPs tend to work best. Install Tailscale on both ends and run `gost -L "http://user:pass@:8888"` at home |
| Small VPS in mainland China | A few dozen CNY per month | Run the same gost command and restrict the security group to your app server's IP |
| Residential proxy provider | Usage-based | |
| ~~Cloudflare Workers / Pages~~ | – | **Does not work for this purpose**: outbound traffic uses Cloudflare's overseas datacenter IPs, and Bilibili returns 412 |

**2. Edge function relay** (if you have no machine in mainland China)

Deploy `scripts/esa-relay.js` to Alibaba Cloud ESA Edge Functions and change its `TOKEN`. Extraction requests to Chinese platforms will then originate from mainland edge nodes:

```bash
PARSE_VIDEO_RELAY_CN=https://<function-domain>/relay
PARSE_VIDEO_RELAY_TOKEN=<same-TOKEN>
```

You can also enable `PARSE_VIDEO_EDGE_IMG=1` / `PARSE_VIDEO_EDGE_MEDIA=1` so users in mainland China fetch images and videos directly from the edge instead of routing through the overseas app server. Limitation: yt-dlp cannot use this relay, so Bilibili is limited to 480p direct URLs through it. Deployment, outbound IP probing, and token handling are covered in [docs/esa-relay.md](docs/esa-relay.md) (Chinese).

**3. Supply login cookies**

```bash
PARSE_VIDEO_XHS_COOKIE="a1=...; web_session=..."
PARSE_VIDEO_BILI_COOKIE="SESSDATA=...; buvid3=..."
```

Copy the `Cookie` request header from your browser's developer tools: F12 → Network. Bilibili also obtains a buvid device fingerprint automatically without login, which is often sufficient. Douyin image posts (note/slides) do not require a configured cookie: when anonymous APIs are restricted, extraction falls back to rendering the desktop page in a temporary incognito Chromium session without an account. Optionally set `PARSE_VIDEO_DOUYIN_COOKIE` to retrieve the full post data, including Live Photo videos, through slidesinfo. Without it, the browser fallback cannot retrieve those Live Photo videos.

**4. Host in mainland China**, then use `PARSE_VIDEO_PROXY` to give YouTube an overseas outbound connection.

</details>

<details>
<summary><b>YouTube bot detection</b>: “Sign in to confirm you're not a bot”</summary>

<br>

Full YouTube support requires EJS scripts, a JavaScript runtime, and a working outbound connection. Docker includes Deno. For manual installations, use `pip install -e ".[web,cli,youtube]"`. The `js_runtime` field in `/api/health` should be `deno` or a supported `node`, rather than `null`.

Compose configures `PARSE_VIDEO_POT_URL=http://bgutil:4416`. The plugin and server are pinned to matching versions; upgrade both together instead of updating just one to `latest`.

Docker also includes a native Chromium session / SABR fallback in an isolated Python interpreter. If ordinary extraction fails or returns an unreadable media URL, it can generate a content-bound token in the same browser session, download the full video through the existing WARP connection, validate it, and cache it for preview, download, and conversion. Full downloads have been verified for two public videos that previously triggered bot detection, without account cookies. High-resolution and audio downloads can also use this path if they fail.

Concurrent extraction requests for the same link share one fetch. Verified YouTube videos are cached by video ID, so long URLs, short URLs, and timestamped share links reuse the same cache. Cache reads do not wait for browser jobs handling other videos. After the default path fails, backup extraction and the native fallback run in parallel; the first result to pass media validation is accepted.

A PO Token does not guarantee that IP restrictions will be lifted. Other deployments can configure `PARSE_VIDEO_PROXY_YOUTUBE` to route only YouTube extraction and media through a dedicated outbound connection. Content that requires login still needs a valid `cookies.txt`. Platform self-checks test two videos and read media samples; one successful extraction does not mark the whole platform as healthy.

See [YouTube troubleshooting](docs/youtube.md) (Chinese) for measured results, limitations, and diagnostics.

Set `PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS=7` to update yt-dlp weekly and restart the app when idle, helping it keep up with platform changes.

</details>

## Performance

Outbound connections are reused throughout extraction, relay requests, and CDN downloads, avoiding repeated DNS lookups and TCP / TLS handshakes. Before-and-after measurements on the same machine:

| | Before | After | Change |
|---|---:|---:|:-:|
| Bilibili extraction, median of 5 videos | 2039 ms | **699 ms** | −66% |
| Single relay request | 1098 ms | **268 ms** | −76% |
| 256 KB Range request for seeking | 1457 ms | **449 ms** | −69% |
| Proxied download throughput | 1.5 MB/s | **1.9 MB/s** | +27% |

Additional measurements:

| Scenario | Result |
|---|---|
| Page rendering | p50 **1.8 ms**; p95 49 ms at 50 concurrent requests; about **730 QPS** with one worker |
| GIF conversion, 20 s / 640 px / 15 fps | **1.9 s**; page p50 rises only from 1.8 ms to 3.1 ms during conversion |
| 109 MB upload | 0.64 s; maximum page latency variation of 37 ms during upload |
| Idle resources | 0.13% CPU, 57 MB RAM |

<details>
<summary>Test environment and limitations</summary>

<br>

Tests used a 4-core VPS running Docker, with an overseas outbound connection relayed through Alibaba Cloud ESA Edge Functions for Chinese platforms. Load tests ran over loopback without a reverse proxy.

These numbers are optimistic; expect lower performance in a real deployment:

- Conversion used synthetic `testsrc` video, which compresses more easily than real footage. GIF conversion of real content is expected to take 2–5 times longer.
- Network RTT and reverse proxy overhead were excluded.
- **Most extraction time is spent waiting for the platform's response, rather than on server computation.** Most of the 699 ms above was the round trip between the relay and the Chinese platform.

Capacity bottlenecks, in order: bandwidth (videos pass through the server; 100 Mbps supports roughly 10 concurrent downloads), FFmpeg CPU usage (each GIF occupies one core for several seconds), then platform rate limits on the outbound IP. Built-in quotas and rate limits default to a 4-core server serving a few hundred daily active users and can be adjusted through environment variables.

**Scaling**: job state is held in process memory. Multiple containers need sticky sessions at the load balancer, using a cookie or IP hash, and the same `PARSE_VIDEO_SECRET`. Job outputs are files on local disk. Fully stateless scaling requires both shared job state and shared storage or forwarding between instances.

</details>

## Configuration

All configuration uses environment variables. Every variable is optional.

<details open>
<summary><b>Basics</b></summary>

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_HOST` / `PARSE_VIDEO_PORT` | `0.0.0.0` / `8000` | Listening address / port |
| `PARSE_VIDEO_DATA_DIR` | `./data` | Directory for caches, outputs, and keys |
| `PARSE_VIDEO_SITE_URL` | Derived from request | Public site URL for canonical links, sitemap, and Open Graph images |
| `PARSE_VIDEO_TRUST_PROXY` | `0` | Set to `1` behind a reverse proxy to trust `X-Forwarded-For` |
| `PARSE_VIDEO_USERNAME` + `PARSE_VIDEO_PASSWORD` | – | Setting both enables Basic Auth |
| `PARSE_VIDEO_MCP` | `1` | Set to `0` to disable `/mcp`; disabled by default in the Docker image |

</details>

<details>
<summary><b>Extraction</b></summary>

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_PROXY` | – | HTTP proxy for extraction across all platforms |
| `PARSE_VIDEO_PROXY_YOUTUBE` | – | Dedicated HTTP proxy for YouTube pages, videos, and images; takes priority over the general proxy, and the PO Token plugin uses the same outbound connection |
| `PARSE_VIDEO_PROXY_CN` | – | Extraction proxy only for Chinese platforms: Douyin, Xiaohongshu, Kuaishou, Bilibili, Weibo, etc.; important when hosting overseas |
| `PARSE_VIDEO_PROXY_CN_MEDIA` | `0` | Set to `1` to proxy video / image transfers through `PROXY_CN` as well; needed only if the CDN returns 403, and consumes proxy bandwidth |
| `PARSE_VIDEO_RELAY_CN` + `PARSE_VIDEO_RELAY_TOKEN` | – | Edge relay URL and token; see `scripts/esa-relay.js` |
| `PARSE_VIDEO_EDGE_IMG` | `0` | Set to `1` so browsers fetch images from Chinese platforms directly through edge nodes; requires `esa-relay.js` with `/img` support |
| `PARSE_VIDEO_EDGE_MEDIA` | `0` | Set to `1` so browsers fetch video / audio from Chinese platforms directly through edge nodes; requires `esa-relay.js` with `/media` support, and traffic counts toward ESA usage |
| `PARSE_VIDEO_XHS_COOKIE` / `PARSE_VIDEO_BILI_COOKIE` / `PARSE_VIDEO_DOUYIN_COOKIE` | – | Login cookie strings for Xiaohongshu / Bilibili / Douyin, useful when overseas requests are blocked; Douyin has an incognito browser fallback without cookies, but image posts need cookies to retrieve Live Photo videos |
| `PARSE_VIDEO_COOKIES_FILE` | `./cookies.txt` | Netscape-format cookie file for yt-dlp |
| `PARSE_VIDEO_COOKIES_BROWSER` | – | Read cookies directly from a browser: `firefox` / `edge` / `chrome` |
| `PARSE_VIDEO_POT_URL` | – | YouTube PO Token service URL, such as `http://bgutil:4416` |
| `PARSE_VIDEO_YOUTUBE_NATIVE` | `1` | Native session / SABR fallback included in Docker; set to `0` to disable; manual installations also require an isolated interpreter |
| `PARSE_VIDEO_YOUTUBE_NATIVE_PROXY` | – | Unauthenticated HTTP proxy for the fallback; defaults to the dedicated YouTube connection, then WARP, then the general proxy |
| `PARSE_VIDEO_YOUTUBE_NATIVE_TIMEOUT` | `75` | Time limit in seconds for a native session download and validation; also subject to the endpoint / job timeout |
| `PARSE_VIDEO_YOUTUBE_HLS` | `1` | Try HLS through the same outbound connection before launching a browser, typically around 10 seconds; use the browser if it fails; set to `0` to use only the browser path |
| `PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS` | `0` | Update yt-dlp every specified number of days and restart when idle |
| `PARSE_VIDEO_PARSE_CACHE` | `600` | Extraction result cache lifetime in seconds |
| `PARSE_VIDEO_DNS_TTL` | `300` | DNS cache lifetime in seconds for SSRF checks |

</details>

<details>
<summary><b>Quotas and rate limits</b></summary>

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_MAX_JOBS` | CPU cores − 1 | Maximum concurrent download / conversion jobs |
| `PARSE_VIDEO_MAX_QUEUE` / `PARSE_VIDEO_JOB_TIMEOUT` | `50` / `300` | Queue limit / per-job timeout in seconds |
| `PARSE_VIDEO_MAX_JOBS_PER_IP` / `PARSE_VIDEO_MAX_STREAMS_PER_IP` | `2` / `4` | Concurrent jobs / download streams per IP |
| `PARSE_VIDEO_RL_PARSE` / `_RL_JOB` / `_RL_UPLOAD` / `_RL_PROXY` | `10` / `20` / `10` / `240` | Per IP: extractions per minute / jobs per 10 minutes / uploads per hour / proxy requests per minute |
| `PARSE_VIDEO_PROXY_GB_PER_DAY` | `10` | Daily download limit through `/api/proxy` per IP, grouping IPv6 by /64; resets at midnight Beijing time; `0` means unlimited |
| `PARSE_VIDEO_MAX_UPLOAD` / `PARSE_VIDEO_MAX_SOURCE` | 300 MB | Upload / source video size limit, configured in bytes |
| `PARSE_VIDEO_DISK_QUOTA` | 8 GB | Quota for `data/`; oldest files are deleted when exceeded |
| `PARSE_VIDEO_SOURCE_TTL` / `PARSE_VIDEO_JOB_TTL` | 2 h / 1 h | Source video cache / conversion output lifetime |
| `PARSE_VIDEO_FFMPEG_THREADS` | Automatic | Threads per FFmpeg process; divided among concurrent jobs by default |

</details>

<details>
<summary><b>Security and SEO</b></summary>

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_SECRET` | Generated in `data/secret.key` | Signing key for proxy URLs; must match across instances |
| `PARSE_VIDEO_SSRF_DNS` | `1` | Set to `0` to disable DNS resolution checks, only for local debugging with a fake-IP proxy |
| `PARSE_VIDEO_SITE_VERIFICATION` | – | Site verification `<meta>` markup, inserted unchanged into `<head>` |
| `PARSE_VIDEO_ANALYTICS` | – | Analytics snippet, such as Baidu Analytics / Umami / GA, inserted at the bottom of pages |

</details>

<details>
<summary><b>Usage statistics</b></summary>

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_STATS_TOKEN` | – | Recording starts only when set; open `/stats?token=<token>` for hourly / daily users, platform success rates, failure reasons, and job durations |
| `PARSE_VIDEO_STATS_IGNORE_IPS` | – | Comma-separated IPs to exclude from statistics; add the server's own public IP; loopback / private IPs are already excluded. On an admin's device, open `/test` and enable test mode once using the statistics token |
| `PARSE_VIDEO_FEEDBACK_REPO` / `_GH_TOKEN` / `_KEY` | – | Failure feedback: private repository for issues, fine-grained token with Issues read/write access only to that repository, and a base64-encoded 32-byte key to encrypt feedback email addresses. These and all four SMTP settings below must be configured to show the feedback action |
| `PARSE_VIDEO_SMTP_USER` / `_PASS` / `_HOST` / `_PORT` | – / – / `smtp.qq.com` / `465` | Sender email, SMTP authorization password, host, and port for notifying users after a reported issue is fixed |
| `PARSE_VIDEO_ALERT_TO` | – | Admin alert email, sent using the SMTP settings above. Hourly checks send an alert if more than half the platforms time out / fail to connect for two consecutive rounds, suggesting a server connectivity issue, or one platform fails extraction for three rounds, suggesting a platform change. Recovery sends another email; repeated alerts for the same issue are suppressed |
| `PARSE_VIDEO_STATS_DAYS` | `90` | Event retention in days |

Data is stored in `data/stats.db` (SQLite). Only events are recorded: successful extractions do not store links, and IP addresses are represented by a keyed HMAC truncated to 12 characters, allowing visitor counts without retaining the original IP. Failed links are stored separately in `data/failures.db` for 7 days and listed on the statistics page for troubleshooting; admin test devices and the server's own requests are excluded. Use `/api/stats?range=7d&token=…` to retrieve JSON directly.

</details>

## API

The website uses these same endpoints. You can call them from shortcuts, scripts, or your own app. The upstream parse-video-py endpoints `/video/share/url/parse`, `/video/id/parse`, and `/mcp` are preserved.

```bash
curl -s "https://ynvan.com/api/parse?url=https://v.douyin.com/8x0AeP8prW8/"
```

```jsonc
{
  "code": 200,
  "data": {
    "source": "douyin",
    "title": "济公爷爷为大家祈福啦…",
    "author": { "name": "济公爷爷·游本昌" },
    "duration": 49.5, "width": 720, "height": 1280,
    "video_url": "https://…/play/….mp4",
    "music_url": "https://….mp3",
    "images": [{ "url": "…", "live_photo_url": "…" }],
    "formats": [{ "label": "720p H.265", "url": "…", "filesize": 126185059 }],
    "sig": { "https://…/play/….mp4": "3f9c…" }          // Signature for each URL; required by the endpoints below
  }
}
```

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/parse?url=` | Extract from share text / links. Failures return a `reason`: `deleted` `login` `blocked` `network` `timeout` `unsupported` `empty` `parse` |
| `GET` | `/api/parse/stream?url=` | Stream real extraction stages, followed by the same final result as the JSON endpoint; used by the website for stage messages, elapsed time, and a fallback indicator |
| `GET` | `/api/proxy?url=&sig=&filename=&download=1` | Proxy direct media URLs with Range support; accepts only URLs signed in extraction results |
| `POST` | `/api/prepare` | `{url \| page_url, sig, title}` caches the source video on the server and returns a `source_id` or background job |
| `POST` | `/api/upload` | Upload a local video as multipart data; returns `source_id` |
| `GET` | `/api/source/{id}` · `/strip?n=` | Source video with seeking support · thumbnail strip |
| `POST` | `/api/convert` | `{source_id, format: gif\|livephoto\|motionphoto, start, end, fps, width, dither, speed, key_time}` |
| `POST` | `/api/live` | `{items: [{image_url, video_url, image_sig, video_sig}], format, title}` packages original platform Live Photos |
| `POST` | `/api/download` | `{page_url, format_spec, sig, title}` downloads and merges media server-side with yt-dlp |
| `GET` | `/api/jobs/{id}` · `/file` · `/preview` · `/video` | Job progress · output file · Live Photo cover · Live Photo MOV |
| `DELETE` | `/api/jobs/{id}` | Cancel a job; only the initiating client may do so |
| `GET` | `/api/health` | Queue, disk usage, and yt-dlp version |

Extraction progress uses SSE: `{type: "progress", stage, fallback}` in `data` reports a real stage, and `{type: "result", result}` carries the final JSON result. The website uses conversational status text, shows actual elapsed time, and keeps a fallback indicator visible once a fallback is activated. Cache hits do not flash a waiting bar. Clearing the input or changing links stops the previous request and timer. Stage events do not expose cookies, PO Tokens, or internal service details. The original JSON extraction endpoint remains available.

Job endpoints return `{id, status: queued|running|done|error, progress, message, filename, filesize}`. Poll `/api/jobs/{id}` until `done`, then fetch `/file`. Rate limits return `429` with a `Retry-After` header.

## Architecture

```mermaid
flowchart LR
    U[Browser / script] -->|/api/parse| P[Extraction layer]
    P --> A[parse-video-py parsers<br/>Douyin · Xiaohongshu · Kuaishou · X · Bilibili …]
    P --> Y[yt-dlp<br/>YouTube · TikTok · fallback]
    P -->|Signed URLs| U
    U -->|/api/proxy| X[Media proxy<br/>Referer · Range · SSRF checks]
    U -->|/api/prepare · convert · live| J[Job queue<br/>Rate limits · quotas · timeouts]
    J --> F[ffmpeg<br/>GIF · MOV · frame extraction]
    J --> L[Live Photo metadata<br/>Apple ContentIdentifier · Motion Photo XMP]
    J -->|/api/jobs| U
```

```text
main.py                          Startup entry point
src/parse_video_py/               Legacy package compatibility; forwards to qiuyuantu on demand
src/qiuyuantu/
  parser/                        Upstream-derived platform parsers; enhanced douyin / redbook / twitter parsers
  parser/ytdlp.py                General yt-dlp parser and quality options
  parser/errors.py               Extraction error classification
  convert/ffmpeg.py              FFmpeg wrapper: GIFs, clip encoding, frames, thumbnail strips, progress
  convert/livephoto.py           Apple MakerNote / MOV identifiers and Motion Photo XMP
  convert/tasks.py               Background jobs: prepare video, convert GIF / Live Photo / Motion Photo, package originals
  convert/fetch.py               Streaming downloads, relay fallback for Chinese CDNs, yt-dlp download / merge
  convert/bilibili.py            Bilibili HD and audio-only downloads: select DASH tracks and merge
  convert/jobs.py · store.py     Job queue, source video cache, disk quotas
  convert/net.py                 SSRF protection, URL signatures, outbound connection pools
  convert/relay.py               Edge function relay transport
  convert/updater.py             Automatic yt-dlp updates
  diag.py                        Admin diagnostics: python -m qiuyuantu.diag <link>
  web/app.py                     FastAPI assembly: background task lifecycle, middleware, routes
  web/legacy_api.py              Upstream-compatible endpoints and response formats
  web/limits.py                  Per-IP rate limits and concurrency quotas
  web/pages.py · parse.py        Pages and SEO landing pages; /api/parse extraction, /api/feedback failure reports
  web/proxy.py · media.py        /api/proxy forwarding; source preparation, conversion, downloads, job queries
  web/admin.py · middleware.py   Health, statistics, test mode; security headers and page view tracking
  feedback.py                    Failure feedback: GitHub issues, encrypted emails, retesting and email notifications
  stats.py                       SQLite event statistics, time-bucket summaries, /stats page
  seo.py · guides.py             Landing pages, tutorials, JSON-LD, sitemap
  templates/                     base / index / guide / guides / stats / 404
  static/                        Self-hosted fonts, subset serif fonts, styles, Open Graph images
  static/js/                     ES modules: app, result, converter, trimmer, save, api, ui, dom,
                                 site animations, stats. Directory served under /js/<content-hash>/
scripts/push_urls.py             Baidu URL submission and sitemap ping
scripts/esa-relay.js             Alibaba Cloud ESA edge function: /probe outbound checks, /relay for Chinese platforms
scripts/subset_fonts.py          Rebuild serif font subsets after copy changes
docker/youtube/worker.py         Native YouTube session downloads and media validation in an isolated interpreter
docker-compose.yml · Caddyfile   HTTPS deployment on a single machine
```

## Security

This service fetches user-provided links and forwards files back to them. Public deployments primarily need to prevent abuse as an open proxy / SSRF relay and crashes caused by malicious media files.

| Protection | Implementation |
|---|---|
| URL signatures | Proxying, source preparation, downloads, and Live Photo packaging accept only URLs signed by `/api/parse` using HMAC, preventing arbitrary proxy use |
| SSRF prevention | Rejects literal private IPs, localhost, `.internal`, and cloud metadata addresses; resolves domains before allowing access; validates every redirect hop, including requests made by platform parsers |
| Connection isolation | Reuses outbound connections without sharing cookie jars between requests |
| Response headers | CSP, `X-Frame-Options: DENY`, `nosniff`, and `Referrer-Policy`; `/docs` and `/openapi.json` are disabled |
| Processes | Containers run as non-root; FFmpeg / yt-dlp use argument lists without a shell; timeouts and cancellations kill child processes; errors do not expose server paths |
| Quotas | Per-IP rate limits, concurrent job limits, upload and source size limits, disk quotas |

FFmpeg and yt-dlp process untrusted media and should be kept up to date. yt-dlp supports automatic updates; update FFmpeg by rebuilding the image. Treat `cookies.txt` as an account credential and store it only on the server. The service has no built-in user account system; rate limits target scripted abuse, and operators need to assess their own bandwidth costs.

## Roadmap

- [x] 30+ platform parsers with yt-dlp fallback
- [x] GIF / iPhone Live Photo / Android Motion Photo conversion
- [x] Original Xiaohongshu and Douyin Live Photo packaging
- [x] Public deployment protections: rate limits, quotas, signatures, SSRF prevention, CSP
- [x] Landing pages, tutorials, and structured data
- [x] Outbound connection reuse, reducing extraction / media request latency by 66% / 69%
- [ ] iOS Shortcut: save extraction results directly to the photo library, including Live Photos
- [ ] Batch operations: extract multiple links / download an entire image gallery as an archive
- [ ] Live Photos from more platforms, including Weibo and Instagram
- [ ] Multi-instance scaling: external job state and shared output storage; moving state alone does not solve local output files
- [ ] Contribute Douyin quality options, Xiaohongshu original images, and the X fallback back to upstream parse-video-py

## Contributing

Issues and pull requests are welcome.

```bash
git clone https://github.com/hiyufan/shizhen.git && cd shizhen
python -m venv .venv && .venv/bin/pip install -e ".[web,cli,dev]"
.venv/bin/python main.py
```

- **Code quality**: Ruff formats and checks Python; see `pyproject.toml` for the 120-character line limit, complexity ≤ 10, import sorting, and common bug checks. ESLint checks frontend scripts; see `eslint.config.mjs` for complexity ≤ 10, nesting ≤ 4 levels, and functions ≤ 80 lines. Failed CI checks block deployment. Run `pre-commit install` to run Ruff automatically on commits; before committing frontend changes, run `npx --yes eslint@10 src/qiuyuantu/static/js`. Tests: `python -m pytest -q`, with no external network access.
- **Layering**: `parser/` handles links → `VideoInfo`, without HTTP endpoints. `convert/` handles downloads, conversion, and the job queue without depending on FastAPI. `web/` validates input, handles authentication and rate limits, and delegates the work. Frontend scripts belong in `static/js/`, organized by feature; do not add inline executable scripts to templates.
- **Adding a platform**: implement a `BaseParser` subclass in `src/qiuyuantu/parser/` and register domains in `parser/__init__.py`. Return `VideoInfo`; directly downloadable quality options belong in `formats` with a `url`, while formats requiring server-side merging use `format_spec`.
- **Editing copy**: landing page content lives in `seo.py`, tutorials in `guides.py`. After editing site copy, run `python scripts/subset_fonts.py --src <NotoSerifCJK-OTF-directory>` to rebuild serif font subsets. The 900 weight includes only large page headings; `tests/test_fonts.py` checks for missing characters. Font URLs include content hashes automatically.
- **Porting upstream fixes**: local parsers have been rewritten to use `BaseParser.get_json / get_text` and raise `ParseError` with a reason. Do not replace them with whole upstream files. Compare changes, port the relevant fix, and add offline cases in `tests/test_small_parsers.py`.
- **Outbound requests**: always use `utils.create_async_client()` or `convert.net.safe_client()`, which provide per-hop SSRF checks and connection pooling. Do not instantiate `httpx.AsyncClient()` directly.
- Before submitting behavior changes, verify one real link each from Douyin, Xiaohongshu, X, and Bilibili, and run each of the three conversion formats once.

## Acknowledgments

- [parse-video-py](https://github.com/wujunwei928/parse-video-py): the project's starting point; some smaller-platform integrations still use the APIs it discovered
- [yt-dlp](https://github.com/yt-dlp/yt-dlp): YouTube and fallback extraction
- [FFmpeg](https://ffmpeg.org/) / [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg): media conversion
- [FastAPI](https://fastapi.tiangolo.com/) · [httpx](https://www.python-httpx.org/) · [piexif](https://github.com/hMatoba/Piexif)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) · [fxtwitter](https://github.com/FixTweet/FxTwitter)
- Fonts: [Noto Serif SC](https://fonts.google.com/noto/specimen/Noto+Serif+SC), [Inter](https://rsms.me/inter/), [Playfair Display](https://github.com/clauseggers/Playfair), [JetBrains Mono](https://www.jetbrains.com/lp/mono/)
- Video in the opening demo: [Big Buck Bunny](https://peach.blender.org/) © Blender Foundation, [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)

## Disclaimer and license

This project is intended for personal learning and research. Respect creators' copyright and do not use it to infringe their rights. Operators are responsible for services they deploy using this project.

[MIT License](LICENSE) © 2026 hiyufan, including the upstream parse-video-py copyright notice.

<div align="center">
<br>
<sub>If you find this project useful, a ⭐ is always appreciated.</sub>
</div>
