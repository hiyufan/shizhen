"""CLI 输出格式化模块，对齐 Go 版 parse-video 的输出格式"""

import dataclasses
import json
import sys

from qiuyuantu.parser.base import VideoInfo


def format_text_output(info: VideoInfo) -> str:
    """文本格式输出（对齐 Go 版 formatTextOutput）"""
    lines = []
    lines.append(f"标题: {info.title}")
    lines.append(f"作者: {info.author.name} (UID: {info.author.uid})")
    if info.video_url:
        lines.append(f"视频地址: {info.video_url}")
    if info.cover_url:
        lines.append(f"封面地址: {info.cover_url}")
    if info.music_url:
        lines.append(f"音乐地址: {info.music_url}")
    if info.images:
        lines.append("图片列表:")
        for i, img in enumerate(info.images, 1):
            if img.live_photo_url:
                lines.append(f"  [{i}] {img.url} (LivePhoto: {img.live_photo_url})")
            else:
                lines.append(f"  [{i}] {img.url}")
    else:
        lines.append("图片数量: 0")
    lines.extend(_content_lines(info))
    return "\n".join(lines)


def _content_lines(info: VideoInfo) -> list[str]:
    lines = [f"素材: {asset.label or asset.ext} {asset.url}" for asset in info.assets]
    lines.extend(f"作品: {entry.title} {entry.url}" for entry in info.entries)
    for document in info.documents:
        lines.extend((f"正文: {document.title}", document.content))
    if info.next_page_url:
        lines.append(f"下一页: {info.next_page_url}")
    if info.animation_frames:
        lines.append(f"动画帧数: {len(info.animation_frames)}（逐帧时序见 JSON 输出）")
    return lines


def format_json_output(info: VideoInfo) -> str:
    """JSON 格式输出"""
    data = dataclasses.asdict(info)
    return json.dumps(data, ensure_ascii=False, indent=2)


def output_result(info: VideoInfo, fmt: str = "text") -> None:
    """输出单条解析结果到 stdout"""
    if fmt == "json":
        print(format_json_output(info))
    else:
        print(format_text_output(info))


def output_batch_error(input_url: str, error_msg: str) -> None:
    """输出批量解析中的错误"""
    print(f"[失败] {input_url}", file=sys.stderr)
    print(f"错误: {error_msg}", file=sys.stderr)
