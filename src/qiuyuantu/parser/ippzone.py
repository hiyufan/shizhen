"""皮皮搞笑 / 最右的视频源文件与清晰度列表，不把水印地址作为高清替代。"""

from .base import FormatInfo, ImgInfo
from .quality import direct_format, number, rank, verify_direct_formats


def first_video(post: dict) -> dict:
    videos = post.get("videos") or {}
    return next((videos[str(image["id"])] for image in post.get("imgs") or [] if str(image.get("id")) in videos), {})


def post_images(post: dict) -> list[ImgInfo]:
    images = []
    videos = post.get("videos") or {}
    for image in post.get("imgs") or []:
        if str(image.get("id")) in videos or image.get("video") or image.get("mp4"):
            continue
        variants = image.get("urls") or {}
        available = [(key, row) for key, row in variants.items() if isinstance(row, dict) and row.get("urls")]
        if not available:
            continue
        _, source = max(
            available,
            key=lambda pair: (
                pair[0] == "origin",
                number(pair[1].get("w")) * number(pair[1].get("h")),
            ),
        )
        images.append(ImgInfo(url=source["urls"][0]))
    return images


async def video_formats(video: dict, referer: str) -> list[FormatInfo]:
    formats = [direct_format(url, video) for key in ("urlsrc", "url", "urlext") if (url := video.get(key))]
    for quality in video.get("qualities") or []:
        # resolution 可能是档位名，不能据此编造宽高；以 MP4 里的视频轨道为准。
        formats.extend(direct_format(row["url"], quality) for row in quality.get("urls") or [] if row.get("url"))
    unique = {}
    for fmt in sorted(formats, key=rank, reverse=True):
        unique.setdefault(fmt.url or fmt.format_spec, fmt)
    return await verify_direct_formats(list(unique.values()), {"Referer": referer}, all_tracks=True)
