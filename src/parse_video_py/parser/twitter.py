"""Twitter / X：支持 twitter.com、x.com、t.co 短链的推文，视频、GIF 和图集。

主路是 X 官方嵌入用的 syndication 接口；敏感 / 墓碑 / 要登录的推文它拿不到，换 fxtwitter 再试一次。
"""

import math
import re

from ..utils import create_async_client
from .base import BaseParser, ImgInfo, VideoAuthor, VideoInfo

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_TWEET_ID = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)")
_CLIP_TYPES = ("video", "animated_gif")


def _best_mp4(variants: list | None) -> str:
    """码率最高的 MP4（m3u8 之类的跳过）。"""
    mp4 = [v for v in variants or [] if v.get("content_type") == "video/mp4" and v.get("url")]
    return max(mp4, key=lambda v: v.get("bitrate") or 0)["url"] if mp4 else ""


def _is_missing(tweet: dict) -> bool:
    return not tweet or tweet.get("__typename") == "TweetTombstone"


def _from_syndication(tweet: dict) -> VideoInfo:
    media = tweet.get("mediaDetails") or []
    clip = next((m for m in media if m.get("type") in _CLIP_TYPES), None) or {}

    video_url = _best_mp4((clip.get("video_info") or {}).get("variants"))
    cover_url = clip.get("media_url_https", "")
    top = tweet.get("video") or {}
    if not video_url and top.get("variants"):
        video_url, cover_url = _best_mp4(top["variants"]), top.get("poster", "")

    images = []
    if not video_url:
        images = [
            ImgInfo(url=m["media_url_https"]) for m in media if m.get("type") == "photo" and m.get("media_url_https")
        ]
    if images:
        cover_url = images[0].url
    if not video_url and not images:
        raise Exception("该推文中没有找到视频或图片")

    size = clip.get("original_info") or {}
    user = tweet.get("user") or {}
    return VideoInfo(
        video_url=video_url,
        cover_url=cover_url,
        title=tweet.get("text", ""),
        images=images,
        duration=float((clip.get("video_info") or {}).get("duration_millis") or 0) / 1000,
        width=size.get("width") or 0,
        height=size.get("height") or 0,
        author=VideoAuthor(
            uid=user.get("id_str", ""),
            name=user.get("name") or user.get("screen_name", ""),
            avatar=user.get("profile_image_url_https", ""),
        ),
    )


def _from_fxtwitter(tweet: dict) -> VideoInfo | None:
    media = (tweet.get("media") or {}).get("all") or []
    clip = next((m for m in media if m.get("type") in ("video", "gif")), None) or {}
    video_url = (_best_mp4(clip.get("variants")) or clip.get("url", "")) if clip else ""
    if video_url:
        images, cover_url = [], clip.get("thumbnail_url", "")
    else:
        images = [ImgInfo(url=m["url"]) for m in media if m.get("type") == "photo" and m.get("url")]
        cover_url = images[0].url if images else ""
    if not video_url and not images:
        return None
    author = tweet.get("author") or {}
    return VideoInfo(
        video_url=video_url,
        cover_url=cover_url,
        title=tweet.get("text") or "",
        images=images,
        duration=float(clip.get("duration") or 0),
        width=clip.get("width") or 0,
        height=clip.get("height") or 0,
        author=VideoAuthor(
            uid=str(author.get("id") or ""),
            name=author.get("name") or author.get("screen_name") or "",
            avatar=author.get("avatar_url") or "",
        ),
    )


class Twitter(BaseParser):
    async def parse_share_url(self, share_url: str) -> VideoInfo:
        # t.co 短链要先跟一次跳转拿到推文地址
        if "t.co/" in share_url:
            share_url = await self._resolve_tco_url(share_url)
        return await self.parse_video_id(self._extract_tweet_id(share_url))

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        tweet = await self._fetch_syndication(video_id)
        if _is_missing(tweet) or not (tweet.get("mediaDetails") or tweet.get("video")):
            # 敏感 / 墓碑 / 需要登录的推文 syndication 拿不到，换 fxtwitter 试一次
            if fx := await self._fetch_fxtwitter(video_id):
                return fx
            if _is_missing(tweet):
                raise Exception("这条推文不存在、已删除或需要登录才能看")
        return _from_syndication(tweet)

    async def _fetch_syndication(self, tweet_id: str) -> dict:
        url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token={self._get_token(tweet_id)}"
        headers = {"User-Agent": _UA, "Accept": "application/json", "Referer": "https://platform.twitter.com/"}
        async with create_async_client() as client:
            response = await client.get(url, headers=headers)
        return response.json() if response.status_code == 200 else {}

    async def _fetch_fxtwitter(self, tweet_id: str) -> VideoInfo | None:
        """fxtwitter 的公开接口，对敏感内容更宽容；拿不到就返回 None"""
        try:
            async with create_async_client(follow_redirects=True) as client:
                response = await client.get(
                    f"https://api.fxtwitter.com/i/status/{tweet_id}", headers={"User-Agent": _UA}, timeout=15
                )
            if response.status_code != 200:
                return None
            tweet = response.json().get("tweet") or {}
        except Exception:  # noqa: BLE001 - 备用通道，失败了就当没有
            return None
        return _from_fxtwitter(tweet)

    async def _resolve_tco_url(self, tco_url: str) -> str:
        """t.co 短链 -> 真实地址（301 / 302 的 location）；没跳转就原样返回。"""
        async with create_async_client(follow_redirects=False) as client:
            response = await client.get(tco_url, headers={"User-Agent": _UA})
        if response.status_code in (301, 302) and (location := response.headers.get("location")):
            return location
        return tco_url

    @staticmethod
    def _extract_tweet_id(share_url: str) -> str:
        """x.com / twitter.com / mobile.twitter.com 的 /<用户>/status/<id>"""
        match = _TWEET_ID.search(share_url)
        if not match:
            raise ValueError(f"无法从 URL 中提取推文 ID: {share_url}")
        return match.group(1)

    @staticmethod
    def _get_token(tweet_id: str) -> str:
        """syndication 接口要的 token：(tweetId / 1e15 * π) 转成字符串，去掉 "0" 和 "."。"""
        return str(float(tweet_id) / 1e15 * math.pi).replace("0", "").replace(".", "")
