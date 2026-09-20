import math
import re

from ..utils import create_async_client
from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo


class Twitter(BaseParser):
    """
    Twitter/X 解析器
    支持解析 twitter.com 和 x.com 的推文
    支持视频、GIF 和图集
    """

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        # 处理 t.co 短链: 需要先跟随重定向获取真实 URL
        if "t.co/" in share_url:
            share_url = await self._resolve_tco_url(share_url)

        # 从 URL 中提取 tweet ID
        tweet_id = self._extract_tweet_id(share_url)
        return await self.parse_video_id(tweet_id)

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        token = self._get_token(video_id)
        api_url = (
            f"https://cdn.syndication.twimg.com/tweet-result?"
            f"id={video_id}&token={token}"
        )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://platform.twitter.com/",
        }

        async with create_async_client() as client:
            response = await client.get(api_url, headers=headers)

        json_data = response.json() if response.status_code == 200 else {}
        # 敏感 / 墓碑 / 需要登录的推文 syndication 拿不到, 换 fxtwitter 试一次
        if not json_data or json_data.get("__typename") == "TweetTombstone" or not (
            json_data.get("mediaDetails") or json_data.get("video")
        ):
            fx = await self._fetch_fxtwitter(video_id)
            if fx:
                return fx
            if not json_data or json_data.get("__typename") == "TweetTombstone":
                raise Exception("这条推文不存在、已删除或需要登录才能看")

        # 提取作者信息
        user_data = json_data.get("user", {})
        author_name = user_data.get("name", "")
        author_screen_name = user_data.get("screen_name", "")
        author_avatar = user_data.get("profile_image_url_https", "")
        author_id = user_data.get("id_str", "")

        # 提取推文文本作为标题
        title = json_data.get("text", "")

        # 提取视频和封面信息
        video_url = ""
        cover_url = ""
        images = []

        # 从 mediaDetails 中查找视频或图片
        media_details = json_data.get("mediaDetails", [])
        if media_details:
            for media in media_details:
                media_type = media.get("type", "")

                if media_type in ("video", "animated_gif"):
                    # 获取封面图
                    cover_url = media.get("media_url_https", "")

                    # 从 variants 中选取最高码率的 mp4
                    video_info = media.get("video_info", {})
                    variants = video_info.get("variants", [])

                    max_bitrate = 0
                    for variant in variants:
                        content_type = variant.get("content_type", "")
                        if content_type != "video/mp4":
                            continue
                        bitrate = variant.get("bitrate", 0)
                        url = variant.get("url", "")
                        if bitrate > max_bitrate or not video_url:
                            max_bitrate = bitrate
                            video_url = url
                    break  # 只取第一个视频

        # 如果没有视频，尝试从顶层 video 字段获取
        if not video_url:
            top_video_variants = json_data.get("video", {}).get("variants", [])
            if top_video_variants:
                cover_url = json_data.get("video", {}).get("poster", "")
                max_bitrate = 0
                for variant in top_video_variants:
                    content_type = variant.get("content_type", "")
                    if content_type != "video/mp4":
                        continue
                    bitrate = variant.get("bitrate", 0)
                    url = variant.get("url", "")
                    if bitrate > max_bitrate or not video_url:
                        max_bitrate = bitrate
                        video_url = url

        # 如果还是没有视频，提取图片
        if not video_url and media_details:
            for media in media_details:
                if media.get("type") == "photo":
                    image_url = media.get("media_url_https", "")
                    if image_url:
                        images.append(ImgInfo(url=image_url))

            # 如果有图片，用第一张作为封面
            if images:
                cover_url = images[0].url

        if not video_url and not images:
            raise Exception("该推文中没有找到视频或图片")

        # 使用 screen_name 作为作者名称的补充
        display_name = author_name if author_name else author_screen_name

        # 时长 / 尺寸
        duration = 0.0
        width = height = 0
        for media in media_details or []:
            if media.get("type") in ("video", "animated_gif"):
                duration = float(media.get("video_info", {}).get("duration_millis") or 0) / 1000
                info = media.get("original_info") or {}
                width, height = info.get("width") or 0, info.get("height") or 0
                break

        video_info = VideoInfo(
            video_url=video_url,
            cover_url=cover_url,
            title=title,
            images=images,
            duration=duration,
            width=width,
            height=height,
            author=VideoAuthor(
                uid=author_id,
                name=display_name,
                avatar=author_avatar,
            ),
        )
        return video_info

    async def _fetch_fxtwitter(self, tweet_id: str) -> VideoInfo | None:
        """fxtwitter 的公开接口, 对敏感内容更宽容; 拿不到就返回 None"""
        try:
            async with create_async_client(follow_redirects=True) as client:
                response = await client.get(
                    f"https://api.fxtwitter.com/i/status/{tweet_id}",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    timeout=15,
                )
            if response.status_code != 200:
                return None
            tweet = response.json().get("tweet") or {}
        except Exception:
            return None
        media_all = (tweet.get("media") or {}).get("all") or []
        video_url, cover_url, duration, width, height = "", "", 0.0, 0, 0
        images = []
        for m in media_all:
            if m.get("type") in ("video", "gif") and not video_url:
                variants = [v for v in m.get("variants") or [] if v.get("content_type") == "video/mp4" and v.get("url")]
                variants.sort(key=lambda v: v.get("bitrate") or 0, reverse=True)
                video_url = variants[0]["url"] if variants else m.get("url", "")
                cover_url = m.get("thumbnail_url", "")
                duration = float(m.get("duration") or 0)
                width, height = m.get("width") or 0, m.get("height") or 0
            elif m.get("type") == "photo" and m.get("url"):
                images.append(ImgInfo(url=m["url"]))
        if video_url:
            images = []
        elif images:
            cover_url = images[0].url
        if not video_url and not images:
            return None
        author = tweet.get("author") or {}
        return VideoInfo(
            video_url=video_url,
            cover_url=cover_url,
            title=tweet.get("text") or "",
            images=images,
            duration=duration,
            width=width,
            height=height,
            author=VideoAuthor(uid=str(author.get("id") or ""), name=author.get("name") or author.get("screen_name") or "",
                               avatar=author.get("avatar_url") or ""),
        )

    async def _resolve_tco_url(self, tco_url: str) -> str:
        """
        解析 t.co 短链接，获取真实 URL
        """
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(
                tco_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    )
                },
            )
            # t.co 会返回 301 重定向
            if response.status_code in (301, 302):
                location = response.headers.get("location", "")
                if location:
                    return location
        return tco_url

    def _extract_tweet_id(self, share_url: str) -> str:
        """
        从推文 URL 中提取 tweet ID
        支持格式:
        - https://x.com/user/status/1234567890
        - https://twitter.com/user/status/1234567890
        - https://mobile.twitter.com/user/status/1234567890
        """
        pattern = r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)"
        match = re.search(pattern, share_url)
        if not match:
            raise ValueError(f"无法从 URL 中提取推文 ID: {share_url}")
        return match.group(1)

    def _get_token(self, tweet_id: str) -> str:
        """
        计算 syndication API 所需的 token
        算法: (tweetId / 1e15 * π) 转换为字符串, 去除 "0" 和 "."
        """
        num = float(tweet_id)
        token = (num / 1e15) * math.pi
        token_str = str(token).replace("0", "").replace(".", "")
        return token_str
