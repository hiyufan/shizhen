# Self-hosting and configuration

[Back to README](../README.en.md) · [简体中文](self-hosting.md)

[Deployment](#production-deployment) · [Configuration](#configuration) · [Security](#security) · [Performance](#performance)

## Production deployment

A VPS with **4 CPU cores / 8 GB RAM / 100 Mbps** is sufficient. `docker-compose.yml` includes four services: the app, [Caddy](https://caddyserver.com/) for reverse proxying with automatic HTTPS, [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) for YouTube PO Tokens, and WARP as a backup outbound connection.

```bash
# Point your domain's DNS to this server first
DOMAIN=your.domain docker compose up -d
```

Without Docker, configure Nginx / Caddy to reverse proxy to `127.0.0.1:8000` and set `PARSE_VIDEO_TRUST_PROXY=1` so rate limiting uses the real client IP.

For diagnostics, run `docker compose exec app python -m qiuyuantu.diag "<share-link>"`. It prints the outbound IP, extraction result, and raw status returned by the platform.

Pushes to `main` that change core or Web runtime files deploy after CI passes, with an automatic rollback if deployment checks fail. Documentation-only and account-assistant-only changes run checks without redeploying the website. Setup instructions: [docs/deploy.md](deploy.md) (Chinese).

### Hosting outside mainland China: Xiaohongshu / Bilibili may reject overseas and datacenter IPs

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

You can also enable `PARSE_VIDEO_EDGE_IMG=1` / `PARSE_VIDEO_EDGE_MEDIA=1` so users in mainland China fetch images and videos directly from the edge instead of routing through the overseas app server. Limitation: yt-dlp cannot use this relay, so Bilibili is limited to 480p direct URLs through it. Deployment, outbound IP probing, and token handling are covered in [docs/esa-relay.md](esa-relay.md) (Chinese).

**3. Supply login cookies**

```bash
PARSE_VIDEO_XHS_COOKIE="a1=...; web_session=..."
PARSE_VIDEO_BILI_COOKIE="SESSDATA=...; buvid3=..."
```

Copy the `Cookie` request header from your browser's developer tools: F12 → Network. Bilibili also obtains a buvid device fingerprint automatically without login, which is often sufficient. Douyin image posts (note/slides) do not require a configured cookie: when anonymous APIs are restricted, extraction falls back to rendering the desktop page in a temporary incognito Chromium session without an account. Optionally set `PARSE_VIDEO_DOUYIN_COOKIE` to retrieve the full post data, including Live Photo videos, through slidesinfo. Without it, the browser fallback cannot retrieve those Live Photo videos.

**4. Host in mainland China**, then use `PARSE_VIDEO_PROXY` to give YouTube an overseas outbound connection.

### YouTube bot detection: “Sign in to confirm you're not a bot”

Full YouTube support requires EJS scripts, a JavaScript runtime, and a working outbound connection. Docker includes Deno. For manual installations, use `pip install -e ".[web,cli,youtube]"`. The `js_runtime` field in `/api/health` should be `deno` or a supported `node`, rather than `null`.

Compose configures `PARSE_VIDEO_POT_URL=http://bgutil:4416`. The plugin and server are pinned to matching versions; upgrade both together instead of updating just one to `latest`.

Docker also includes a native Chromium session / SABR fallback in an isolated Python interpreter. If ordinary extraction fails or returns an unreadable media URL, it can generate a content-bound token in the same browser session, download the full video through the existing WARP connection, validate it, and cache it for preview, download, and conversion. Full downloads have been verified for two public videos that previously triggered bot detection, without account cookies. High-resolution and audio downloads can also use this path if they fail.

Concurrent extraction requests for the same link share one fetch. Verified YouTube videos are cached by video ID, so long URLs, short URLs, and timestamped share links reuse the same cache. Cache reads do not wait for browser jobs handling other videos. After the default path fails, backup extraction and the native fallback run in parallel; the first result to pass media validation is accepted.

A PO Token does not guarantee that IP restrictions will be lifted. Other deployments can configure `PARSE_VIDEO_PROXY_YOUTUBE` to route only YouTube extraction and media through a dedicated outbound connection. Content that requires login still needs a valid `cookies.txt`. Platform self-checks test two videos and read media samples; one successful extraction does not mark the whole platform as healthy.

See [YouTube troubleshooting](youtube.md) (Chinese) for measured results, limitations, and diagnostics.

Set `PARSE_VIDEO_YTDLP_AUTOUPDATE_DAYS=7` to update yt-dlp weekly and restart the app when idle, helping it keep up with platform changes.

## Anonymous stickers and Channels

Telegram pack extraction uses publicly cataloged Stickers.wiki assets and requires no Bot token. For local TGS conversion install `pip install -e ".[web,cli,stickers]"`; the Web Docker image includes the renderer. Catalog coverage may differ from Telegram’s current packs.

Discord uses only the official anonymous `/api/v10/sticker-packs` catalog, matching pack IDs, store SKUs or sticker IDs. Individual pack and sticker API endpoints currently require authentication and are not called. APNG uses the existing image decoder; Lottie JSON uses the same `stickers` dependency and isolated render worker as TGS. Raw JSON is limited to 1 MB and retains dimension, frame, duration, total pixel and external resource limits. Private servers and stickers outside the catalog are not supported.

Channels extraction first reads the official anonymous `shortUri` preview. When video is missing, the [public BugPk service](https://api.bugpk.com/) supplements it only if both title and author match. No account cookies, tokens or API keys are sent. A real public video was fully downloaded and converted. Provider quality labels may differ from actual dimensions and are not displayed as verified resolution.

`PARSE_VIDEO_CHANNELS_PUBLIC_FALLBACK=1` is the default; set it to `0` to use only the official preview. Calls are serialized per process with at least 1.05 seconds between starts. Congestion, throttling, identity mismatches and provider failures retain the official cover. Multi-process or multi-instance deployments must enforce the provider's published QPS=1 at a shared egress. The public service has no availability guarantee.

## Configuration

All configuration uses environment variables. Every variable is optional.

### Basics

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_HOST` / `PARSE_VIDEO_PORT` | `0.0.0.0` / `8000` | Listening address / port |
| `PARSE_VIDEO_DATA_DIR` | `./data` | Directory for caches, outputs, and keys |
| `PARSE_VIDEO_SITE_URL` | Derived from request | Public site URL for canonical links, sitemap, and Open Graph images |
| `PARSE_VIDEO_TRUST_PROXY` | `0` | Set to `1` behind a reverse proxy to trust `X-Forwarded-For` |
| `PARSE_VIDEO_USERNAME` + `PARSE_VIDEO_PASSWORD` | – | Setting both enables Basic Auth |
| `PARSE_VIDEO_MCP` | `1` | Set to `0` to disable `/mcp`; disabled by default in the Docker image |

### Extraction

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

### Quotas and rate limits

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

### Security and SEO

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_SECRET` | Generated in `data/secret.key` | Signing key for proxy URLs; must match across instances |
| `PARSE_VIDEO_SSRF_DNS` | `1` | Set to `0` to disable DNS resolution checks, only for local debugging with a fake-IP proxy |
| `PARSE_VIDEO_SITE_VERIFICATION` | – | Site verification `<meta>` markup, inserted unchanged into `<head>` |
| `PARSE_VIDEO_ANALYTICS` | – | Analytics snippet, such as Baidu Analytics / Umami / GA, inserted at the bottom of pages |

### Usage statistics

| Variable | Default | Description |
|---|---|---|
| `PARSE_VIDEO_STATS_TOKEN` | – | Recording starts only when set; open `/stats?token=<token>` for hourly / daily users, platform success rates, failure reasons, and job durations |
| `PARSE_VIDEO_STATS_IGNORE_IPS` | – | Comma-separated IPs to exclude from statistics; add the server's own public IP; loopback / private IPs are already excluded. On an admin's device, open `/test` and enable test mode once using the statistics token |
| `PARSE_VIDEO_FEEDBACK_REPO` / `_GH_TOKEN` / `_KEY` | – | Failure feedback: private repository for issues, fine-grained token with Issues read/write access only to that repository, and a base64-encoded 32-byte key to encrypt feedback email addresses. These and all four SMTP settings below must be configured to show the feedback action |
| `PARSE_VIDEO_SMTP_USER` / `_PASS` / `_HOST` / `_PORT` | – / – / `smtp.qq.com` / `465` | Sender email, SMTP authorization password, host, and port for notifying users after a reported issue is fixed |
| `PARSE_VIDEO_ALERT_TO` | – | Admin alert email, sent using the SMTP settings above. Hourly checks send an alert if more than half the platforms time out / fail to connect for two consecutive rounds, suggesting a server connectivity issue, or one platform fails extraction for three rounds, suggesting a platform change. Recovery sends another email; repeated alerts for the same issue are suppressed |
| `PARSE_VIDEO_STATS_DAYS` | `90` | Event retention in days |

Data is stored in `data/stats.db` (SQLite). Only events are recorded: successful extractions do not store links, and IP addresses are represented by a keyed HMAC truncated to 12 characters, allowing visitor counts without retaining the original IP. Failed links are stored separately in `data/failures.db` for 7 days and listed on the statistics page for troubleshooting; admin test devices and the server's own requests are excluded. Use `/api/stats?range=7d&token=…` to retrieve JSON directly.

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
