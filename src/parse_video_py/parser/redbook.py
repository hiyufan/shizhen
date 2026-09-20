import json
import re

import fake_useragent

from ..utils import create_async_client
from .base import BaseParser, FormatInfo, ImgInfo, VideoAuthor, VideoInfo

# 原图: ci.xiaohongshu.com 上按 token 取, w/0 表示不缩放; 默认的 urlDefault 是缩到 1080 宽的版本
_ORIGINAL_IMAGE = "https://ci.xiaohongshu.com/{token}?imageView2/2/w/0/format/jpg/q/90"


class RedBook(BaseParser):
    """
    小红书
    """

    async def parse_share_url(self, share_url: str) -> VideoInfo:
        headers = {
            "User-Agent": fake_useragent.UserAgent(os=["windows"]).random,
        }
        async with create_async_client(follow_redirects=True) as client:
            response = await client.get(share_url, headers=headers)
            response.raise_for_status()

        pattern = re.compile(
            pattern=r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("parse video json info from html fail")

        # 页面里的 JSON 混着 JS 的 undefined, 换成 null 再用 json 解析 (比 yaml 快一个量级)
        json_data = json.loads(re.sub(r"\bundefined\b", "null", find_res.group(1).strip()))

        note_id = json_data["note"]["currentNoteId"]
        # 验证返回：小红书的分享链接有有效期，过期后会返回 undefined
        if not note_id or note_id == "undefined":
            raise Exception("parse fail: note id in response is undefined")
        detail = (json_data.get("note", {}).get("noteDetailMap") or {}).get(note_id) or {}
        data = detail.get("note")
        if not data:
            raise Exception("parse fail: note detail is empty (链接可能过期或需要 xsec_token)")

        # 视频地址: h264 里挑分辨率最高的做默认 (浏览器能直接播), 其余档位和 h265 放进 formats
        video_url = ""
        formats = []
        width = height = 0
        duration = 0.0
        stream = (data.get("video") or {}).get("media", {}).get("stream", {}) or {}
        h264_data = [s for s in stream.get("h264") or [] if s.get("masterUrl")]
        if h264_data:
            h264_data.sort(key=lambda s: ((s.get("width") or 0) * (s.get("height") or 0), s.get("videoBitrate") or 0), reverse=True)
            best = h264_data[0]
            video_url = best["masterUrl"]
            width, height = best.get("width") or 0, best.get("height") or 0
            duration = float(best.get("duration") or 0) / 1000
            seen = {(width, height)}
            for s in h264_data[1:]:
                key = (s.get("width") or 0, s.get("height") or 0)
                if key in seen:
                    continue
                seen.add(key)
                formats.append(FormatInfo(label=f"{min(key)}p", url=s["masterUrl"], height=min(key), filesize=int(s.get("size") or 0)))
            for s in stream.get("h265") or []:
                if s.get("masterUrl"):
                    formats.append(FormatInfo(label=f"{min(s.get('width') or 0, s.get('height') or 0)}p H.265", url=s["masterUrl"],
                                              height=min(s.get("width") or 0, s.get("height") or 0), filesize=int(s.get("size") or 0), codec="H.265"))
                    break
        if not duration:
            duration = float((data.get("video") or {}).get("capa", {}).get("duration") or 0)

        # 获取图集图片地址 (原图分辨率)
        images = []
        if len(video_url) <= 0:
            for img_item in data.get("imageList") or []:
                url_default = img_item.get("urlDefault") or img_item.get("urlPre") or ""
                if not url_default:
                    continue
                token = url_default.split("/")[-1].split("!")[0]
                img_info = ImgInfo(url=_ORIGINAL_IMAGE.format(token=token) if token else url_default)
                # 是否有 livephoto 视频地址
                if img_item.get("livePhoto", False) and (
                    live_h264 := (img_item.get("stream") or {}).get("h264", [])
                ):
                    img_info.live_photo_url = live_h264[0]["masterUrl"]
                images.append(img_info)

        cover = (data.get("imageList") or [{}])[0].get("urlDefault", "")
        video_info = VideoInfo(
            video_url=video_url,
            cover_url=cover,
            title=data.get("title") or (data.get("desc") or "")[:60],
            images=images,
            duration=duration,
            width=width,
            height=height,
            formats=formats,
            author=VideoAuthor(
                uid=data["user"]["userId"],
                name=data["user"]["nickname"],
                avatar=data["user"]["avatar"],
            ),
        )
        return video_info

    async def parse_video_id(self, video_id: str) -> VideoInfo:
        raise NotImplementedError("小红书暂不支持直接解析视频ID")
