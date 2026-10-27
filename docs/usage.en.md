# Usage and supported platforms

[Back to README](../README.en.md) · [简体中文](usage.md)

[Features](#features) · [Supported platforms](#supported-platforms) · [Music covers](#qq-music-and-netease-cloud-music-cover-downloader) · [Other assets](#steam-assets-hoyoplay-wallpapers-and-apple-music-artwork)

## Features

### Extraction

- Original watermark-free video streams
- Original Xiaohongshu images, commonly 1920×2560
- Douyin HD H.265 video and separate background music
- Server-side merging for YouTube / Bilibili HD formats, up to 4K where available
- Live Photo detection and packaging for an entire post
- QQ Music / NetEase Cloud Music song, album and playlist cover extraction
- Media proxy for browser playback when direct requests return 403
- Errors classified as deleted content, login required, rate limited, and more

### Conversion

- **GIF**: up to 30 seconds, 5–30 fps, 160–960 px wide, 0.5–3× speed, two-pass palette encoding
- **iPhone Live Photo**: up to 10 seconds, paired JPG + MOV, with `ContentIdentifier` written to both files and verified by reading it back
- **Android Motion Photo**: JPG with an embedded MP4, Motion Photo + MicroVideo XMP metadata
- **Original packaging**: remux platform Live Photos without re-encoding
- **Local videos**: upload and convert files up to 300 MB
- **Animated music covers**: decode the actual image format and convert to GIF, Live Photo or Android Motion Photo

### Website

- Minimal editorial design with full mobile support
- Self-hosted fonts; no external requests for page assets
- Tool pages and tutorials with JSON-LD structured data
- Public deployment protections: rate limits, quotas, signatures, SSRF prevention, CSP
- Docker runs as a non-root user
- Automatic yt-dlp updates to keep up with platform changes
- Admin statistics at `/stats`: hourly / daily usage, platform success rates, and failure reasons

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
| QQ Music | – | Cover art | – | 800px for songs / albums, 1000px for playlists | Song, album and playlist share links; cover art only |
| NetEase Cloud Music | – | Cover art | – | Original dimensions supplied by the platform | Song, album and playlist share links; download animated covers in their original format or convert to GIF / Live Photo / Motion Photo |
| Steam Points Shop | ✅ | GIF / APNG assets | – | Original assets supplied by Steam | Individual item links; animated avatars, transparent frames, stickers and MP4 / WebM backgrounds; animations can be converted |
| HoYoPlay | ✅ | Current launcher wallpapers | – | Original assets supplied by the platform | Choose one of four games for current China-launcher backgrounds; available videos can be converted; no historical archive |
| Apple Music | Animated artwork | Cover art | – | Images requested up to 3000px; video up to 1080p | Public song, album and playlist artwork; animations shown when available; no song audio |
| Weibo, Xigua, Pipixia, AcFun, TikTok, … | ✅ | Partial | – | | 30+ platforms, with yt-dlp as the fallback for others |

> Extraction depends on each platform's web structure and may temporarily break when it changes. Upstream parse-video-py is updated frequently. Port relevant fixes into `parser/` by comparing changes; do not overwrite local parsers wholesale. See [Contributing](development.en.md#contributing).

## QQ Music and NetEase Cloud Music cover downloader

Paste a song, album or playlist share link to extract its artwork. **A playlist link returns the playlist’s own cover, not the album cover of its first track.** No browser extension or sign-in to this app is needed.

| What you need | Tool and result |
|---|---|
| QQ Music song / album / playlist cover | [QQ Music cover downloader](https://ynvan.com/qq-music-cover): public sources may supply 800px song and album artwork or 1000px playlist covers |
| NetEase Cloud Music original cover | [NetEase cover downloader](https://ynvan.com/netease-music-cover): save the dimensions actually supplied by the platform |
| Animated playlist cover | Keep the original animated WebP / GIF, identifying the actual format even when its URL ends in `.jpg` |
| Animated artwork to GIF / Live Photo | Convert directly after extraction to GIF, an iPhone Live Photo bundle or Android Motion Photo; see the [guide](https://ynvan.com/guide/music-cover-download) |

For example, paste `https://music.163.com/playlist?id=14005122656` to extract that playlist’s animated cover and make a GIF. Still covers remain still images. This feature does not download song audio or batch-download artwork for every playlist track.

An iPhone webpage cannot write a Live Photo directly into Photos. Import paired JPG + MOV files on a Mac, or save the video and convert it with a phone app. Android Motion Photos require a compatible gallery.

## Steam assets, HoYoPlay wallpapers and Apple Music artwork

| Need | Online tool and output |
|---|---|
| Steam animated avatars, transparent frames and stickers | [Steam assets](https://ynvan.com/steam-assets): paste an individual `/reward/` item link, keep GIF / APNG or convert to transparent GIF; [guide](https://ynvan.com/guide/steam-animated-assets) |
| Steam background videos | Download the supplied MP4 / WebM, or trim it into a GIF or Live Photo |
| Genshin, Star Rail, ZZZ and Honkai Impact 3rd launcher backgrounds | [HoYoPlay wallpapers](https://ynvan.com/hoyoplay-wallpaper): choose a game and save current public China-launcher stills and available videos; [guide](https://ynvan.com/guide/hoyoplay-wallpaper-download) |
| Apple Music still and animated artwork | [Apple Music artwork](https://ynvan.com/apple-music-cover): song, album or playlist links; save public animations as MP4 or convert to GIF / Live Photo |

Steam’s large preview may be still; this tool selects its public animation file, including APNGs named `.png`. Saving media does not unlock items in a Steam account. HoYoPlay reads current assets without a historical archive. Apple Music extracts public artwork only, without song audio. Animated asset availability depends on the current platform response.

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="screenshot-convert.png" alt="Conversion panel: select a clip on the thumbnail strip and create a Live Photo"></td>
    <td width="50%"><img src="screenshot-guide.png" alt="Tutorial page"></td>
  </tr>
  <tr>
    <td align="center"><sub>Turn a clip from any video into a Live Photo and choose its cover frame</sub></td>
    <td align="center"><sub>Each tutorial answers a specific question</sub></td>
  </tr>
</table>
