import asyncio
import contextlib
import json
import os
import re
from urllib.parse import parse_qs, urlparse

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo, _random_ua
from .errors import ParseError

# slidesinfo 的两个入口主机，按优先级排。同一个接口两个域名都挂着，走的是不同的
# CDN 边缘。热连接实测（各 15 次）：www.douyin.com p50 ~200ms / p90 ~220ms，
# www.iesdouyin.com p50 226~266ms / p90 273~349ms——前者不仅快，尾部也稳得多。
# 后者留作退路：某个域名被风控或出口不通时，换一个往往就过了。
_SLIDES_HOSTS = ("www.douyin.com", "www.iesdouyin.com")

# 图文兜底浏览器同时开几个页面：几个人同时贴图文时不用排队（每个 3~4 秒）。
# 页面共用一个 context，cookie 和过掉的人机验证都是共享的
_BROWSER_PAGES = max(1, int(os.environ.get("PARSE_VIDEO_DOUYIN_PAGES", "2") or 2))


class _WarmBrowser:
    """进程内常驻的无痕 Chromium。

    抖音对全新浏览器环境会随机弹人机验证，同一 context 过一次后后续加载稳定
    放行（实测连续 4 次加载全过、图集 7 张图一张不少），所以浏览器和 context
    起了就不关。只有图文兜底解析用它。全程无账号。

    页面每次现开、用完就关：停在抖音页面上自动播放的推荐流 / 实况会让渲染进程
    和软件合成的 GPU 进程一直转（实测停首页 83%、实况图文 106% CPU）；切到
    about:blank 虽然不转了，渲染进程攒下的内存却不还（7 次解析后单页 1GB）。
    关掉页面两样都还干净，cookie 在 context 里不受影响，新开页面只多几十毫秒。
    某个页面出错只影响它自己；浏览器进程断了才整个重起。
    """

    def __init__(self, size: int) -> None:
        self._sem = asyncio.Semaphore(size)
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._context = None

    async def _ensure_context(self):
        from playwright.async_api import async_playwright

        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._context
            await self._close_all()
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                chromium_sandbox=False,
                args=["--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            # UA 在 context 创建时定死：cookie 和 TLS 会话都绑着它，中途换 UA
            # 等于自曝。playwright 的 headless 会被识破，webdriver 标志要藏掉。
            self._context = await self._browser.new_context(
                locale="zh-CN",
                user_agent=_random_ua("Windows"),
                viewport={"width": 1280, "height": 900},
            )
            await self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            return self._context

    @contextlib.asynccontextmanager
    async def page(self):
        """开一个页面用，同时最多 size 个，用完（不管成败）关掉。"""
        async with self._sem:
            context = await self._ensure_context()
            page = await context.new_page()
            try:
                yield page
            finally:
                with contextlib.suppress(Exception):
                    await page.close()

    async def _close_all(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                with contextlib.suppress(Exception):
                    await closer.close()
        self._context = self._browser = None
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None

    async def reset(self) -> None:
        """进程退出时整个收掉。"""
        async with self._lock:
            await self._close_all()


_warm_browser = _WarmBrowser(_BROWSER_PAGES)


async def warmup_browser() -> None:
    """启动时后台预热兜底浏览器：起进程 + 首次导航过掉人机验证，别让第一个
    解析图文的用户垫这十几秒。playwright 没装或起不来就静默放弃。"""
    with contextlib.suppress(Exception):
        async with _warm_browser.page() as page:
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)


async def aclose_browser() -> None:
    """进程退出时把常驻浏览器收掉。"""
    await _warm_browser.reset()


def _note_images(dom: dict) -> list[ImgInfo]:
    """浏览器兜底取到的图配上实况。

    lives 是页面 awemeInfo 里 {图片 uri 末段: 实况地址}，按 id 出现在图片地址里来配；
    awemeInfo 说没有实况就是没有。lives 为 None（没找到 awemeInfo）时退回 DOM：
    只有一张图时播放器里那段视频必定是它的；多张时页面只留前后几页的 <video>，
    硬配会张冠李戴，宁可不给。"""
    lives = dom.get("lives")
    images = []
    for u in dom.get("images") or []:
        path = u.split("?")[0]
        live = next((src for img_id, src in (lives or {}).items() if img_id in path), "")
        images.append(ImgInfo(url=u, live_photo_url=live))
    if lives is None and len(images) == 1 and dom.get("live"):
        images[0].live_photo_url = dom["live"]
    return images


def _looks_like_note(url: str) -> bool:
    """地址是不是图文作品。

    /note/{id}、/share/note/{id}/、/share/slides/{id}/ 能从路径看出来；但图集的
    短链常被 302 成 /share/video/{id}（路径完全一样），只能靠跳转参数：
    is_slides=1 或 schema_type=37（两条已知图集分享都是 37）。
    """
    try:
        parsed = urlparse(url)
        parts = parsed.path.split("/")
        query = parse_qs(parsed.query)
    except Exception:  # noqa: BLE001
        return False
    if "note" in parts or "slides" in parts:
        return True
    if query.get("is_slides", [""])[0] == "1":
        return True
    return query.get("schema_type", [""])[0] == "37"


def _configured_cookie() -> str:
    """站长配的登录 cookie（PARSE_VIDEO_DOUYIN_COOKIE），没配返回空串。"""
    return os.getenv("PARSE_VIDEO_DOUYIN_COOKIE", "")


# 图文兜底：从渲染好的 PC 版页面 DOM 里取图集数据。图集图是 /tos-cn-i- 开头的
# 大图（naturalWidth >= 300），推荐流缩略图和头像都被这条滤掉；同一张图会在
# 主图 + 封面 + 缩略条出现多次，去掉 ~tplv-... 模板后按路径查重。
_NOTE_DOM_EXTRACT = """
() => {
  // 只在播放器里找：右侧相关推荐的缩略图原图也有 330~1440 宽，混进来会在图集末尾
  // 多出一张（第一项就是本条「播放中」，表现为重复图）。推荐列表有时也挂在
  // note-detail 底下，所以要收窄到 player-container，轮播的每一页都在它里面
  const detail = document.querySelector('[data-e2e="note-detail"]');
  const root = (detail || document).querySelector('[data-e2e="player-container"]') || detail || document;
  const seen = {};
  for (const img of root.querySelectorAll('img')) {
    const src = img.currentSrc || img.src || '';
    try {
      const u = new URL(src);
      if (!/(^|\\.)douyinpic\\.com$/.test(u.hostname)) continue;
      if (!u.pathname.startsWith('/tos-cn-i-')) continue;
      if (img.naturalWidth > 0 && img.naturalWidth < 300) continue;
      const key = u.pathname.replace(/~[^/]*$/, '');
      if (!seen[key]) seen[key] = src;   // 浏览器实际用过的地址才有效
    } catch {}
  }
  const music = [...document.querySelectorAll('video')]
    .map(v => v.currentSrc || v.src || '')
    .find(s => s.includes('ies-music/') || s.endsWith('.mp3')) || '';
  // 实况：播放器的 React 组件 props 里有整条作品的数据（往上第 4 层左右的 awemeInfo），
  // images[i].video.playAddr 就是每张图的实况，按图片 uri 末段和 DOM 里的图对上。
  // 多张实况的轮播 DOM 只留前后三页的 <video>，靠 DOM 配不全，只能走这里
  // total 是 awemeInfo 里的总张数，轮询时据此判断图齐了没有（见 _extract_note_dom）
  let lives = null, total = null;   // null = 没找到 awemeInfo；{} = 找到了但没有实况
  try {
    const fk = Object.keys(root).find(k => k.startsWith('__reactFiber$'));
    for (let f = fk && root[fk], up = 0; f && up < 30; f = f.return, up++) {
      const imgs = f.memoizedProps && f.memoizedProps.awemeInfo && f.memoizedProps.awemeInfo.images;
      if (!Array.isArray(imgs)) continue;
      lives = {};
      total = imgs.length;
      for (const im of imgs) {
        let src = (((im && im.video && im.video.playAddr) || [])[0] || {}).src || '';
        if (src.startsWith('//')) src = 'https:' + src;
        const id = ((im && im.uri) || '').split('/').pop();
        if (src && id) lives[id] = src;
      }
      break;
    }
  } catch {}
  // 拿不到 awemeInfo（页面改版）时的退路：单张实况的页面直接用播放器放那段 2~5 秒的
  // 视频，地址是 .../video/tos/... 或 /aweme/v1/play/?file_id=（备用 <source>）。
  // 背景音乐（ies-music、/obj/tos-cn-ve-）和 H.265 探测片 uuu_265.mp4 不是这个路径
  const players = [...root.querySelectorAll('video')].map(v => {
    const src = [v.currentSrc || v.src, ...[...v.querySelectorAll('source')].map(s => s.src)]
      .find(s => /\\/video\\/tos\\/|\\/aweme\\/v1\\/play\\//.test(s || ''));
    return src ? { src, playing: !v.paused } : null;
  }).filter(Boolean);
  const live = ((players.find(l => l.playing) || players[0]) || {}).src || '';
  const metadesc = document.querySelector('meta[name="description"]')?.content || '';
  const desc = (document.querySelector('[data-e2e="note-desc"]')?.innerText
      || metadesc || document.title).trim().replace(/\\s*-\\s*抖音\\s*$/, '');
  // 作者：从头像图往上找最近的用户链接（querySelector 不含自身，链接本身是
  // 上一层的 A 也要认）。昵称经常是空文本，再从 meta 的「XX于YYYYMMDD发布在抖音」里兜底
  let author = '', uid = '';
  const avatar = document.querySelector('img[src*="aweme-avatar"]');
  if (avatar) {
    let node = avatar.parentElement;
    for (let i = 0; i < 5 && node && !uid; i++, node = node.parentElement) {
      const a = (node.matches && node.matches('a[href*="/user/"]')) ? node
          : (node.querySelector && node.querySelector('a[href*="/user/"]'));
      if (a) uid = (a.getAttribute('href') || '').split('/').pop().split('?')[0];
      const text = a && a.innerText && a.innerText.trim();
      if (text && text.length <= 30) author = text;
    }
  }
  if (!author) {
    const m = metadesc.match(/([^\\s，。]{1,30})于\\d{8}发布在抖音/);
    if (m) author = m[1];
  }
  return { images: Object.values(seen), music, lives, live, total, detail: !!detail, desc, author, uid };
}
"""


_PC_HOSTS = ("www.iesdouyin.com", "www.douyin.com")
_ROUTER_DATA = re.compile(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", re.DOTALL)
_SSR_PAGE_KEYS = ("video_(id)/page", "note_(id)/page")
# 页面上取不到正文时用的是 <meta name="description">，抖音在后面拼了一段 SEO 文字：
# 「不好 是台风 - 今日有雪223于20260711发布在抖音，已经收获了2281.9万个喜欢，来抖音，记录美好生活！」
_META_DESC_TAIL = re.compile(r"\s+-\s+[^\n]*?于\d{8}发布在抖音[\s\S]*$")


def _note_title(desc: str) -> str:
    return _META_DESC_TAIL.sub("", desc).strip()


def _first(urls: list | None) -> str:
    return (urls or [""])[0] or ""


def _prefer_non_webp(urls: list | None) -> str:
    """优先非 .webp 的地址（地址带签名参数，要看 ? 前面的路径）；都是 webp 就用第一个。"""
    urls = urls or []
    return next((u for u in urls if u and not u.split("?", 1)[0].endswith(".webp")), _first(urls))


def _aweme_images(aweme: dict) -> list[ImgInfo]:
    """图集的每张图（不要 webp），实况图带上那段短视频。
    注意 download_url_list 是带用户名水印的（tplv-dy-water-v2），不能用。"""
    images = []
    for img in aweme.get("images") or []:
        if url := _prefer_non_webp(img.get("url_list")):
            live = _first(((img.get("video") or {}).get("play_addr") or {}).get("url_list"))
            images.append(ImgInfo(url=url, live_photo_url=live))
    return images


def _aweme_author(aweme: dict) -> VideoAuthor:
    author = aweme.get("author") or {}
    return VideoAuthor(
        uid=author.get("sec_uid", ""),
        name=author.get("nickname", ""),
        avatar=_first((author.get("avatar_thumb") or {}).get("url_list")),
    )


def _video_music(aweme: dict) -> str:
    """视频的背景音乐在 music.play_url 里（图集不一样，见 _build）。"""
    music = (aweme.get("music") or {}).get("play_url") or {}
    return _first(music.get("url_list")) or music.get("uri", "")


def _collect_formats(video_data: dict, primary: dict) -> list[FormatInfo]:
    """从 bit_rate 档位里挑出和默认直链不同的清晰度 / 编码，每个 (高度, 编码) 只留码率最高的一档"""
    best: dict = {}
    for br in video_data.get("bit_rate") or []:
        pa = br.get("play_addr") or {}
        urls = pa.get("url_list") or []
        if not urls or pa.get("url_key") == primary.get("url_key"):
            continue
        h = pa.get("height") or 0
        w = pa.get("width") or 0
        # 竖屏视频 height 才是长边，统一用短边当「清晰度」
        short = min(w, h) if w and h else h
        codec = "H.265" if br.get("is_h265") else ""
        key = (short, codec)
        if key in best and best[key].filesize >= (pa.get("data_size") or 0):
            continue
        best[key] = FormatInfo(
            label=f"{short}p" + (f" {codec}" if codec else ""),
            url=urls[0].replace("playwm", "play"),
            ext="mp4",
            height=short,
            filesize=int(pa.get("data_size") or 0),
            codec=codec,
        )
    primary_short = min(primary.get("width") or 0, primary.get("height") or 0)
    # 只保留比默认直链更清晰的，或者同清晰度但体积更小的 H.265
    keep = [f for f in best.values() if f.height > primary_short or (f.codec and f.height == primary_short)]
    return sorted(keep, key=lambda f: (-f.height, f.codec))


def _aweme_from_router_data(router: dict) -> dict:
    """老的 SSR 页面数据（window._ROUTER_DATA）里取作品。"""
    if "loaderData" not in router:
        raise Exception("Unknown data structure")
    loader = router["loaderData"]
    page_key = next((k for k in _SSR_PAGE_KEYS if k in loader), None)
    if page_key is None:
        raise Exception("failed to parse Videos or Photo Gallery info from json")
    video_info = loader[page_key]["videoInfoRes"]
    if not video_info["item_list"]:
        filters = video_info["filter_list"]
        raise Exception(filters[0]["detail_msg"] if filters else "failed to parse video info from HTML")
    return video_info["item_list"][0]


class DouYin(BaseParser):
    """
    抖音 / 抖音火山版
    """

    _note = False  # 地址上看出来是图文（/note/、/slides/）

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        video_id = await self._resolve_video_id(share_url)
        try:
            aweme = await self._fetch_aweme(video_id)
        except ParseError as err:
            if err.reason not in ("restricted", "login"):
                raise
            # 图文被平台 filter：API 的匿名通路已经全被堵死（slidesinfo 服务端
            # filter、feed 接口拿推荐流凑数、detail 接口要 a_bogus+UIFID 签名），
            # 用常驻无痕浏览器渲染 PC 版页面兜底，纯匿名、不碰任何登录态。
            # 不看 _note 就直接试：图集短链会被 302 成 /share/video/{id}，路径
            # 判定不可靠；真视频进来也会被浏览器里 /note/{id}→/video/{id} 的
            # 归一跳转识别出来交回原错。浏览器这条路也没有（没装 playwright /
            # 页面拿不到数据）才把 slidesinfo 的错误抛出去。
            info = await self._note_via_browser(video_id)
            if info is None:
                raise
            return info
        return await self._build(aweme)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        return await self.parse_share_url(self._get_request_url_by_video_id(video_id))

    async def _resolve_video_id(self, share_url: str) -> str:
        """电脑网页端链接从路径里取；App 分享短链 https://v.douyin.com/xxxxxx 要跟一次跳转。"""
        host = urlparse(share_url).netloc
        if host in _PC_HOSTS:
            self._note = _looks_like_note(share_url)
            if video_id := self._parse_video_id_from_path(share_url):
                return video_id
            raise ValueError("Failed to parse video ID from PC share URL")
        if host == "v.douyin.com":
            if video_id := await self._parse_app_share_url(share_url):
                return video_id
            raise ValueError("Failed to parse video ID from app share URL")
        raise ValueError(f"Douyin not support this host: {host}")

    async def _fetch_aweme(self, video_id: str) -> dict:
        # 优先通过专用接口获取视频 / 图集详情（同时返回 aweme_details）
        if slides := await self._get_slides_info(video_id):
            return slides["aweme_details"][0]
        # 回退到旧的 HTML SSR 解析。2026-09 实测：抖音已经不在 SSR 里渲染
        # videoInfoRes 了，_ROUTER_DATA 只剩页面骨架（ua / query 这些），
        # 能正常解析的作品走这条路一样拿不到。留着只当 slidesinfo 临时抽风时的
        # 安全网，别指望它——真要修抖音解析，从 slidesinfo 那条路查起。
        return _aweme_from_router_data(await self._ssr_router_data(self._get_request_url_by_video_id(video_id)))

    async def _ssr_router_data(self, page_url: str) -> dict:
        headers = self.get_default_headers()
        if douyin_cookie := _configured_cookie():
            headers["Cookie"] = douyin_cookie
        # 以前靠「第一次拿 ttwid、带着再请求一次」来换数据。实测 cookie 根本存不下来
        # （池化客户端不攒 cookie，走中继时 ESA 也不透传 Set-Cookie），重试纯属白跑一趟
        async with create_async_client(follow_redirects=True) as client:
            response = await client.get(page_url, headers=headers)
            response.raise_for_status()
        found = _ROUTER_DATA.search(response.text)
        if not found or not found.group(1):
            raise ValueError("parse video json info from html fail")
        if "videoInfoRes" not in found.group(1):
            # 两条路都没拿到数据。注意别归成 restricted：SSR 这条路对所有作品
            # 都失效，拿它当「平台限制」的证据会把网络抖动也误报进去。
            raise ParseError("parse", "slidesinfo 无数据，SSR 也没渲染 videoInfoRes")
        return json.loads(found.group(1).strip())

    async def _build(self, aweme: dict) -> VideoInfo:
        video = aweme.get("video") or {}
        # 浏览器预览 / 默认下载用 H.264 (play_addr_h264)，其余清晰度档位放进 formats
        primary = video.get("play_addr_h264") or video.get("play_addr") or {}
        has_primary = bool(primary.get("url_list"))
        images = _aweme_images(aweme)
        if images:
            # 图集：没有视频，video 里的地址打不开；video.play_addr.uri 才是背景音乐
            video_url, formats = "", []
            music_url = primary.get("uri", "") if has_primary else ""
        else:
            video_url = await self._playable_url(_first(primary.get("url_list")).replace("playwm", "play"))
            formats = _collect_formats(video, primary)
            music_url = _video_music(aweme)
        return VideoInfo(
            video_url=video_url,
            cover_url=_prefer_non_webp((video.get("cover") or {}).get("url_list")),
            music_url=music_url,
            title=aweme.get("desc", ""),
            images=images,
            duration=float(video.get("duration") or 0) / 1000,
            width=(primary.get("width") or 0) if has_primary else 0,
            height=(primary.get("height") or 0) if has_primary else 0,
            formats=formats,
            author=_aweme_author(aweme),
        )

    async def _playable_url(self, video_url: str) -> str:
        """老的 HTML 路径给的是 aweme.snssdk.com/aweme/v1/play/ 跳转地址，要跟一次 302；
        slidesinfo 接口给的已经是 CDN 直链，不必再多发一次请求。"""
        if "/aweme/v1/play" in video_url:
            return await self.get_video_redirect_url(video_url)
        return video_url

    async def get_video_redirect_url(self, video_url: str) -> str:
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(video_url, headers=self.get_default_headers())
        # 返回重定向后的地址，如果没有重定向则返回原地址（抖音中的西瓜视频，重定向地址为空）
        return response.headers.get("location") or video_url

    def _get_request_url_by_video_id(self, video_id) -> str:
        return f"https://www.iesdouyin.com/share/video/{video_id}/"

    async def _parse_app_share_url(self, share_url: str) -> str:
        """解析app分享链接 https://v.douyin.com/xxxxxx"""
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(share_url, headers=self.get_default_headers())

        location = response.headers.get("location")
        if not location:
            return ""

        # 抖音的分享链接有时会跳到西瓜视频。西瓜已并入抖音，作品 ID 就是 aweme_id，
        # 路径里的数字照取、照常走 slidesinfo 即可（见 xigua.py）。取不到数字时
        # 要自己说清楚原因：返回空的话上层统一抛 "Failed to parse video ID"，
        # 会被 classify 的 deleted 规则收走，对用户谎称"内容已被删除"。
        self._note = _looks_like_note(location)
        video_id = self._parse_video_id_from_path(location)
        if not video_id and "ixigua.com" in location:
            raise ParseError("unsupported", "这条分享链接跳转到了西瓜视频的非作品页")
        return video_id

    @staticmethod
    def _parse_video_id_from_path(url: str) -> str:
        """从地址里取作品 ID：精选页的 modal_id，或者路径里最靠后的一段纯数字。

        https://www.douyin.com/jingxuan?modal_id=7555093909760789812
        https://www.iesdouyin.com/share/video/7424432820954598707/?region=CN&mid=7424432976273869622
        https://www.douyin.com/video/xxxxxx  /  https://www.douyin.com/note/xxxxxx

        aweme_id 是一串纯数字。以前无脑取路径最后一段，短链跳到个人主页、
        活动页这类不带作品 ID 的地址时，会把 "user" 之类当 ID 拿去查接口，
        最后报出来的原因驴唇不对马嘴。
        """
        if not url:
            return ""
        try:
            parsed = urlparse(url)
        except ValueError:
            return ""
        if modal_id := parse_qs(parsed.query).get("modal_id"):
            return modal_id[0]
        return next((part for part in reversed(parsed.path.split("/")) if part.isdigit()), "")

    async def _get_slides_info(self, video_id: str) -> dict:
        """获取抖音视频或图集的详细信息，包括 Live Photo"""
        # 普通视频不带 request_source 可以拿到数据；图文（note）需要带
        # request_source=200。跳转地址里已经看出是图文，就先发后者，省掉先撞一次
        # filter 的往返。
        plain = f"aweme_ids=%5B{video_id}%5D"
        queries = [plain, f"{plain}&request_source=200"]
        if self._note:
            queries.reverse()

        # 抖音对某些作品会返回 status_code=0 + aweme_details=null + filter_list，
        # 表示"接口正常，但这条不对外给数据"（作者限制分享 / 要登录 / 被限流）。
        # 只有两个入口都没拿到数据才算数：正常视频带 request_source=200 也会被 filter。
        filtered = None

        # 2026-09 起抖音对匿名请求收紧图文（note/slides）数据：同一条图文前一晚
        # 还能解析，第二天起 filter reason=4/8；实测 ttwid / Referer / msToken /
        # 换主机都无效，feed 接口对被 filter 的 ID 直接返回推荐流凑数，分享页 SSR
        # 只剩骨架。普通视频不受影响。剩下的通路只有带登录 cookie 请求本接口
        # （PARSE_VIDEO_DOUYIN_COOKIE，和 redbook 的 XHS_COOKIE 同一个玩法）。
        cookie = _configured_cookie()
        headers = self.get_default_headers()
        if cookie:
            headers["Cookie"] = cookie

        async with create_async_client() as client:
            for query in queries:
                for host in _SLIDES_HOSTS:
                    try:
                        response = await client.get(
                            f"https://{host}/web/api/v2/aweme/slidesinfo/?{query}",
                            headers=headers,
                        )
                        response.raise_for_status()
                        data = response.json()
                    except Exception:
                        continue  # 这个主机没拿到, 换一个
                    if data and data.get("aweme_details"):
                        return data
                    if data and data.get("filter_list"):
                        # 平台明确说了"这条不给"，换主机也一样，试下一个 query
                        filtered = data["filter_list"][0]
                        break

        if filtered:
            # filter_list 一般只给个 reason 码，没有 detail_msg；有就带上
            detail = filtered.get("detail_msg") or filtered.get("notice") or ""
            detail = detail or f"抖音 filter reason={filtered.get('reason')}"
            if not cookie and self._note:
                # 没配 cookie 时，图文被 filter 是平台不给匿名数据而不是作者限制：
                # 报 restricted 会让用户以为链接有问题，站长也看不出配 cookie 能修。
                raise ParseError("login", detail)
            raise ParseError("restricted", detail)

        return None

    async def _note_via_browser(self, video_id: str) -> VideoInfo | None:
        """slidesinfo 被平台 filter 后的图文兜底：用常驻无痕 Chromium 渲染 PC 版
        图文页，从渲染结果里取数据。

        实测（2026-09）真浏览器匿名也能打开这些图文页，而 curl/httpx 无论带什么
        cookie 都过不去——字节 WAF 校验 TLS 指纹，还要 JS 算出来的短时效 cookie
        （_waftokenid 约 5 分钟一换），这些只有真浏览器能搞定。PC 版页面已是
        React RSC 结构，数据在渲染时才补齐签名图片 URL，所以直接从 DOM 取，
        不去解析页内载荷。全新浏览器环境会被随机弹人机验证，同一 context 过一次
        之后稳定放行，所以浏览器常驻复用（见 _WarmBrowser），全程无账号。

        playwright 没装或浏览器起不来时返回 None，上层维持 slidesinfo 的报错。
        实况：从播放器 React 组件的 awemeInfo.images[].video.playAddr 取，多张实况
        也能逐张配上；拿不到 awemeInfo 时只给单张图配播放器里那段 <video>。
        """
        page_url = f"https://www.douyin.com/note/{video_id}"
        dom = None
        for attempt in range(2):
            try:
                async with _warm_browser.page() as page:
                    # 第一次 12 秒还没到 DOMContentLoaded 多半是跨境连接卡死了（正常
                    # 1~1.5 秒，统计里有 25~40 秒的长尾），换个页面重来比干等划算；第二次
                    # 放宽到 25 秒，网络只是慢的时候别两次都掐掉
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=12000 if attempt == 0 else 25000)
                    if f"/note/{video_id}" not in page.url:
                        if f"/video/{video_id}" in page.url:
                            # 抖音把 /note/{id} 归一成 /video/{id}：这是条视频，
                            # 播放地址走 blob/HLS，DOM 里拿不到直链，交回上层报原错
                            return None
                        # 跳去首页/推荐流说明这条作品没了
                        raise ParseError("deleted", f"抖音图文页跳转到了 {page.url[:60]}")
                    dom = await self._extract_note_dom(page)
            except ImportError:
                return None
            except ParseError:
                raise
            except Exception:  # noqa: BLE001
                # 页面超时 / 崩了 / 浏览器起不来：出错的页面已经关掉，换一个再来
                continue
            if dom.get("images"):
                break
            if dom.get("total") == 0:
                return None  # awemeInfo 里没有图：不是图文，交回上层报原错
            # 一张图都没等到：多半是撞上人机验证的变体页，或者这条其实是视频（视频走
            # /note/ 地址不会跳 /video/，页面上没有图文区块）。再试一次，还不行才算失败
        else:
            return None

        images = _note_images(dom)
        return VideoInfo(
            video_url="",
            cover_url="",
            title=_note_title(dom.get("desc") or ""),
            music_url=dom.get("music") or "",
            images=images,
            author=VideoAuthor(uid=dom.get("uid") or "", name=dom.get("author") or ""),
            page_url=page_url,
        )

    @staticmethod
    async def _extract_note_dom(page) -> dict:
        """轮询到图集齐了就返回。

        awemeInfo 里有总张数（打开后 ~1.5 秒可读），DOM 里的图一够数就返回，实测
        打开后 1.6~2.0 秒；以前固定等 1 秒再等图数连续两轮（隔 1.5 秒）不变，要
        4~5 秒。读不到 awemeInfo（页面改版）时退回老规则：图数 1.5 秒不再变。
        推荐流 / 合集的图不在 player-container 里，见 _NOTE_DOM_EXTRACT。

        页面中途自己重载（视频走 /note/ 地址、WAF 验证后刷新）会让 evaluate 抛
        「Execution context was destroyed」，接着轮询就行——以前这会被当成浏览器
        坏了，整个重起。一直没有图文区块（视频页 / 验证页）6 秒就放弃。
        """
        loop = asyncio.get_running_loop()
        start = loop.time()
        dom: dict = {"images": []}
        prev, changed_at = -1, start
        while True:
            now = loop.time()
            if now - start >= 15:
                return dom
            try:
                dom = await page.evaluate(_NOTE_DOM_EXTRACT)
            except Exception as err:  # noqa: BLE001
                if "context was destroyed" not in str(err) and "navigat" not in str(err):
                    raise
                await page.wait_for_timeout(200)
                continue
            n = len(dom.get("images") or [])
            total = dom.get("total")
            if total == 0 or (total and n >= total):
                return dom
            if n != prev:
                prev, changed_at = n, now
            elif n and now - changed_at >= (3 if total else 1.5):
                return dom  # 知道总数却一直凑不齐（轮播没全渲染）就别等满 15 秒了
            if not dom.get("detail") and now - start >= 6:
                return dom
            await page.wait_for_timeout(200)
