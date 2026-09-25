import json
import re
import secrets
import string
from urllib.parse import parse_qs, urlparse

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo
from .errors import ParseError


class DouYin(BaseParser):
    """
    抖音 / 抖音火山版
    """

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        # 解析URL获取域名
        parsed_url = urlparse(share_url)
        host = parsed_url.netloc

        if host in ["www.iesdouyin.com", "www.douyin.com"]:
            # 支持电脑网页端链接
            video_id = self._parse_video_id_from_path(share_url)
            if not video_id:
                raise ValueError("Failed to parse video ID from PC share URL")
            share_url = self._get_request_url_by_video_id(video_id)
        elif host == "v.douyin.com":
            # 支持app分享链接 https://v.douyin.com/xxxxxx
            video_id = await self._parse_app_share_url(share_url)
            if not video_id:
                raise ValueError("Failed to parse video ID from app share URL")
            share_url = self._get_request_url_by_video_id(video_id)
        else:
            raise ValueError(f"Douyin not support this host: {host}")

        # 优先通过专用接口获取视频/图集详情。该接口当前同时返回
        # aweme_details，不再依赖页面 SSR 中的 videoInfoRes 字段。
        json_data = await self._get_slides_info(video_id)

        if not json_data:
            # 回退到旧的 HTML SSR 解析。2026-09 实测：抖音已经不在 SSR 里渲染
            # videoInfoRes 了，_ROUTER_DATA 只剩页面骨架（ua / query 这些），
            # 能正常解析的作品走这条路一样拿不到。留着只当 slidesinfo 临时抽风时的
            # 安全网，别指望它——真要修抖音解析，从 slidesinfo 那条路查起。
            pattern = re.compile(
                pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
                flags=re.DOTALL,
            )
            headers = self.get_default_headers()
            async with create_async_client(follow_redirects=True) as client:
                for _attempt in range(2):
                    response = await client.get(share_url, headers=headers)
                    response.raise_for_status()
                    find_res = pattern.search(response.text)
                    if find_res and "videoInfoRes" in find_res.group(1):
                        break
                    # 原本靠"第一次拿 ttwid、带着再请求一次"来换数据。实测 cookie
                    # 根本没存下来（走中继时 ESA 不透传 Set-Cookie），没拿到新
                    # cookie 就重试纯属白跑一趟。
                    if not client.cookies:
                        break

            if not find_res or not find_res.group(1):
                raise ValueError("parse video json info from html fail")

            if "videoInfoRes" not in find_res.group(1):
                # 两条路都没拿到数据。注意别归成 restricted：SSR 这条路对所有作品
                # 都失效，拿它当"平台限制"的证据会把网络抖动也误报进去。
                raise ParseError("parse", "slidesinfo 无数据，SSR 也没渲染 videoInfoRes")

            json_data = json.loads(find_res.group(1).strip())

        # 处理不同的数据结构
        data = None
        if isinstance(json_data, dict) and "aweme_details" in json_data:
            # 专用API返回的数据结构
            if len(json_data["aweme_details"]) > 0:
                data = json_data["aweme_details"][0]
        elif isinstance(json_data, dict) and "loaderData" in json_data:
            # 标准HTML解析返回的数据结构
            VIDEO_ID_PAGE_KEY = "video_(id)/page"
            NOTE_ID_PAGE_KEY = "note_(id)/page"

            original_video_info = None
            if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
                original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY][
                    "videoInfoRes"
                ]
            elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
                original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY][
                    "videoInfoRes"
                ]
            else:
                raise Exception(
                    "failed to parse Videos or Photo Gallery info from json"
                )

            # 如果没有视频信息，获取并抛出异常
            if len(original_video_info["item_list"]) == 0:
                err_detail_msg = "failed to parse video info from HTML"
                if len(filter_list := original_video_info["filter_list"]) > 0:
                    err_detail_msg = filter_list[0]["detail_msg"]
                raise Exception(err_detail_msg)

            data = original_video_info["item_list"][0]
        else:
            raise Exception("Unknown data structure")

        if not data:
            raise Exception("Failed to extract data from response")

        # 获取图集图片地址
        images = []
        # 如果data含有 images，并且 images 是一个列表
        if "images" in data and isinstance(data["images"], list):
            # 获取每个图片的url_list中的第一个元素，优先获取非 .webp 格式的图片 url
            for img in data["images"]:
                if (
                    "url_list" in img
                    and isinstance(img["url_list"], list)
                    and len(img["url_list"]) > 0
                ):
                    # 注意 download_url_list 是带用户名水印的 (tplv-dy-water-v2), 不能用
                    image_url = self._get_no_webp_url(img["url_list"])
                    if image_url:
                        live_photo_url = ""
                        if (
                            "video" in img
                            and "play_addr" in img["video"]
                            and "url_list" in img["video"]["play_addr"]
                        ):
                            live_photo_url = (
                                img["video"]["play_addr"]["url_list"][0]
                                if img["video"]["play_addr"]["url_list"]
                                else ""
                            )
                        images.append(
                            ImgInfo(url=image_url, live_photo_url=live_photo_url)
                        )

        # 获取视频和音频播放地址
        # 浏览器预览 / 默认下载用 H.264 (play_addr_h264), 其余清晰度档位放进 formats
        video_url = ""
        music_url = ""
        formats = []
        width = height = 0
        duration = 0.0
        video_data = data.get("video") or {}
        primary = video_data.get("play_addr_h264") or video_data.get("play_addr") or {}
        if primary.get("url_list"):
            video_url = primary["url_list"][0].replace("playwm", "play")
            width, height = primary.get("width") or 0, primary.get("height") or 0
            music_url = primary.get("uri", "")
        if video_data.get("duration"):
            duration = float(video_data["duration"]) / 1000
        formats = self._collect_formats(video_data, primary)

        # 如果图集地址不为空时，因为没有视频，上面抖音返回的视频地址无法访问，置空处理
        if len(images) > 0:
            video_url = ""
            formats = []
        else:
            # 视频的背景音乐在 music.play_url 里; 图集时 video.play_addr.uri 才是音频
            music = (data.get("music") or {}).get("play_url") or {}
            music_url = (music.get("url_list") or [""])[0] or music.get("uri", "")

        # 老的 HTML 路径给的是 aweme.snssdk.com/aweme/v1/play/ 跳转地址, 要跟一次 302;
        # slidesinfo 接口给的已经是 CDN 直链, 不必再多发一次请求
        video_mp4_url = ""
        if len(video_url) > 0:
            if "/aweme/v1/play" in video_url:
                video_mp4_url = await self.get_video_redirect_url(video_url)
            else:
                video_mp4_url = video_url

        # 获取封面图片，优先获取非 .webp 格式的图片 url
        cover_url = ""
        if (
            "video" in data
            and "cover" in data["video"]
            and "url_list" in data["video"]["cover"]
        ):
            cover_url = self._get_no_webp_url(data["video"]["cover"]["url_list"])

        video_info = VideoInfo(
            video_url=video_mp4_url,
            cover_url=cover_url,
            music_url=music_url,
            title=data.get("desc", ""),
            images=images,
            duration=duration,
            width=width,
            height=height,
            formats=formats,
            author=VideoAuthor(
                uid=data.get("author", {}).get("sec_uid", ""),
                name=data.get("author", {}).get("nickname", ""),
                avatar=(
                    data.get("author", {})
                    .get("avatar_thumb", {})
                    .get("url_list", [""])[0]
                    if data.get("author", {}).get("avatar_thumb", {}).get("url_list")
                    else ""
                ),
            ),
        )
        return video_info

    @staticmethod
    def _collect_formats(video_data: dict, primary: dict) -> list:
        """从 bit_rate 档位里挑出和默认直链不同的清晰度 / 编码, 每个 (高度, 编码) 只留码率最高的一档"""
        best: dict = {}
        for br in video_data.get("bit_rate") or []:
            pa = br.get("play_addr") or {}
            urls = pa.get("url_list") or []
            if not urls or pa.get("url_key") == primary.get("url_key"):
                continue
            h = pa.get("height") or 0
            w = pa.get("width") or 0
            # 竖屏视频 height 才是长边, 统一用短边当"清晰度"
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
        # 只保留比默认直链更清晰的, 或者同清晰度但体积更小的 H.265
        keep = [f for f in best.values() if f.height > primary_short or (f.codec and f.height == primary_short)]
        return sorted(keep, key=lambda f: (-f.height, f.codec))

    async def get_video_redirect_url(self, video_url: str) -> str:
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(video_url, headers=self.get_default_headers())
        # 返回重定向后的地址，如果没有重定向则返回原地址(抖音中的西瓜视频,重定向地址为空)
        return response.headers.get("location") or video_url

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        req_url = self._get_request_url_by_video_id(video_id)
        return await self.parse_share_url(req_url)

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
        video_id = self._parse_video_id_from_path(location)
        if not video_id and "ixigua.com" in location:
            raise ParseError("unsupported", "这条分享链接跳转到了西瓜视频的非作品页")
        return video_id

    def _parse_video_id_from_path(self, url_path: str) -> str:
        """从URL路径中解析视频ID"""
        if not url_path:
            return ""

        try:
            parsed_url = urlparse(url_path)

            # 判断网页精选页面的视频
            # https://www.douyin.com/jingxuan?modal_id=7555093909760789812
            query_params = parse_qs(parsed_url.query)
            if "modal_id" in query_params:
                return query_params["modal_id"][0]

            # 判断其他页面的视频
            # https://www.iesdouyin.com/share/video/7424432820954598707/?region=CN&mid=7424432976273869622&u_code=0
            # https://www.douyin.com/video/xxxxxx  /  https://www.douyin.com/note/xxxxxx
            # aweme_id 是一串纯数字。以前无脑取路径最后一段，短链跳到个人主页、
            # 活动页这类不带作品 ID 的地址时，会把 "user" 之类当 ID 拿去查接口，
            # 最后报出来的原因驴唇不对马嘴。
            path = parsed_url.path.strip("/")
            if path:
                for part in reversed(path.split("/")):
                    if part.isdigit():
                        return part
        except Exception:
            pass

        return ""

    def _get_no_webp_url(self, url_list: list) -> str:
        """优先获取非 .webp 格式的图片 url"""
        if not url_list:
            return ""

        # 优先获取非 .webp 格式的图片 url (地址带签名参数, 要看 ? 前面的路径)
        for url in url_list:
            if url and not url.split("?", 1)[0].endswith(".webp"):
                return url

        # 如果没找到，使用第一项
        return url_list[0] if url_list and url_list[0] else ""

    def _is_note_content(self, html_content: str, share_url: str) -> bool:
        """检查是否是图集内容"""
        try:
            # 方法1: 检查canonical URL是否包含/note/
            pattern = re.compile(
                r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^' r'"\']+)["\']',
                re.IGNORECASE,
            )
            match = pattern.search(html_content)
            if match:
                canonical_url = match.group(1)
                if "/note/" in canonical_url:
                    return True

            # 方法2: 检查URL路径是否包含note相关路径
            parsed_url = urlparse(share_url)
            if "/note/" in parsed_url.path:
                return True

            # 方法3: 检查HTML中是否有图集相关的标识
            if "note_" in html_content or "图文" in html_content:
                return True

        except Exception:
            pass

        return False

    async def _get_slides_info(self, video_id: str) -> dict:
        """获取抖音视频或图集的详细信息，包括 Live Photo"""
        # 普通视频不带 request_source 可以拿到数据；
        # 图文（note）需要带 request_source=200。这里逐个尝试。
        api_urls = [
            (
                f"https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"
                f"?aweme_ids=%5B{video_id}%5D"
            ),
            (
                f"https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"
                f"?aweme_ids=%5B{video_id}%5D&request_source=200"
            ),
        ]

        # 抖音对某些作品会返回 status_code=0 + aweme_details=null + filter_list，
        # 表示"接口正常，但这条不对外给数据"（作者限制分享 / 要登录 / 被限流）。
        # 只有两个入口都没拿到数据才算数：正常视频带 request_source=200 也会被 filter。
        filtered = None

        async with create_async_client() as client:
            for api_url in api_urls:
                try:
                    response = await client.get(
                        api_url, headers=self.get_default_headers()
                    )
                    response.raise_for_status()
                    data = response.json()
                except Exception:
                    continue
                if data and data.get("aweme_details"):
                    return data
                if data and data.get("filter_list"):
                    filtered = data["filter_list"][0]

        if filtered:
            # filter_list 一般只给个 reason 码，没有 detail_msg；有就带上
            detail = filtered.get("detail_msg") or filtered.get("notice") or ""
            raise ParseError("restricted", detail or f"抖音 filter reason={filtered.get('reason')}")

        return None

    def _generate_fixed_length_numeric_id(self, length: int) -> str:
        """生成固定位数的随机数字ID"""
        return "".join(secrets.choice(string.digits) for _ in range(length))

    def _rand_seq(self, n: int) -> str:
        """生成随机字符串"""
        chars = string.ascii_letters + string.digits
        return "".join(secrets.choice(chars) for _ in range(n))
