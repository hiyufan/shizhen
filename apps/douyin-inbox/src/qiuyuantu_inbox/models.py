"""账号任务的配置与输入边界。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, fields
from urllib.parse import urlparse

from qiuyuantu.utils import extract_url


@dataclass(frozen=True)
class Settings:
    workers: int = 2
    poll_seconds: float = 5.0
    send_interval: float = 5.0
    connect_timeout: float = 5.0
    max_pending: int = 300
    sender_pending: int = 3
    sender_hourly: int = 30
    max_attempts: int = 3
    lease_seconds: int = 300
    job_timeout: int = 180
    max_files: int = 30
    file_bytes: int = 20 << 20
    video_bytes: int = 50 << 20
    job_bytes: int = 100 << 20
    disk_bytes: int = 2 << 30
    cache_seconds: int = 3600
    scan_pages: int = 20
    conversations_per_poll: int = 10
    choice_seconds: float = 600.0
    gif_seconds: float = 6.0
    gif_width: int = 480
    gif_fps: int = 12
    event_buffer: int = 1000
    event_poll_seconds: float = 1.0
    scan_seconds: float = 60.0
    encode_workers: int = 1
    photo_batch: int = 3
    commands_per_minute: int = 12
    control_reserve: int = 20
    max_queue_seconds: float = 1800.0
    native_pipeline: bool = False
    upload_workers: int = 2
    upload_streams: int = 2
    upload_bytes: int = 100 << 20
    upload_timeout: float = 180.0
    ack_timeout: float = 60.0
    ack_window: int = 1
    choice_warmup: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        values = {}
        for field in fields(cls):
            default = getattr(defaults, field.name)
            name = f"PARSE_VIDEO_INBOX_{field.name.upper()}"
            raw = os.environ.get(name)
            values[field.name] = cls._env_value(raw, default)
        config = cls(**values)
        config.validate()
        return config

    @staticmethod
    def _env_value(raw: str | None, default):
        if raw is None:
            return default
        if isinstance(default, bool):
            if raw.lower() not in {"true", "false", "1", "0"}:
                raise ValueError("布尔配置只接受 true / false / 1 / 0")
            return raw.lower() in {"true", "1"}
        return type(default)(raw)

    def validate(self) -> None:
        if any(
            not math.isfinite(value) or value <= 0
            for field in fields(self)
            if not isinstance(value := getattr(self, field.name), bool)
        ):
            raise ValueError("账号队列配置必须为正数")
        if self.lease_seconds <= self.job_timeout:
            raise ValueError("lease_seconds 必须大于 job_timeout")
        if self.sender_pending > self.max_pending or max(self.file_bytes, self.video_bytes) > self.job_bytes:
            raise ValueError("账号队列配额设置互相矛盾")
        if self.gif_seconds > 10 or not 120 <= self.gif_width <= 960 or not 4 <= self.gif_fps <= 24:
            raise ValueError("GIF 最长 10 秒，宽度 120–960，帧率 4–24")
        if self.photo_batch > 9 or self.encode_workers > self.workers:
            raise ValueError("图片批次最多 9 张，编码并发不能超过准备 worker")
        if self.ack_window > 2 or self.upload_workers > 2 or self.upload_streams > 2:
            raise ValueError("账号流水线最多两路上传和两条在途确认")
        if (
            max(self.upload_timeout, self.ack_timeout + self.send_interval) >= self.lease_seconds
            or self.upload_bytes < self.video_bytes + self.file_bytes
        ):
            raise ValueError("上传超时必须小于租约，上传字节池必须容纳一组实况")


@dataclass(frozen=True)
class Message:
    id: str
    conversation: str
    sender: str
    created: float
    kind: str
    data: dict

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.conversation}\0{self.id}".encode()).hexdigest()

    def dump(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and any(
            host == domain or host.endswith("." + domain) for domain in ("douyinpic.com", "byteimg.com")
        )
    except ValueError:
        return False


def message_from_snapshot(raw: dict) -> Message | None:
    if raw.get("from_me") or not all(raw.get(key) for key in ("id", "conversation", "sender", "created")):
        return None
    created = float(raw["created"])
    if not math.isfinite(created) or created <= 0:
        return None
    kind, data = raw.get("kind"), raw.get("data") or {}
    if kind == "text":
        from .choices import parse_selection
        from .controls import parse_control

        if command := parse_control(str(data.get("text", ""))):
            return Message(str(raw["id"]), str(raw["conversation"]), str(raw["sender"]), created, "control", command)
        if selected := parse_selection(str(data.get("text", ""))):
            return Message(str(raw["id"]), str(raw["conversation"]), str(raw["sender"]), created, "selection", selected)
        url = extract_url(str(data.get("text", ""))) or ""
        if (urlparse(url).hostname or "").lower() not in ("v.douyin.com", "www.douyin.com", "www.iesdouyin.com"):
            return None
        kind, data = "link", {"url": url}
    valid = kind == "work" and str(data.get("work_id", "")).isascii() and str(data.get("work_id", "")).isdigit()
    valid = valid or (kind == "media" and media_url(data.get("url", "")))
    valid = valid or (
        kind in {"live", "photo"}
        and media_url(data.get("url", ""))
        and str(raw["id"]).isascii()
        and str(raw["id"]).isdigit()
        and (kind == "photo" or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(data.get("live_vid", ""))) is not None)
        and re.fullmatch(r"[a-fA-F0-9]{32}", str(data.get("md5", ""))) is not None
    )
    valid = valid or kind == "link"
    if not valid:
        return None
    return Message(str(raw["id"]), str(raw["conversation"]), str(raw["sender"]), created, kind, data)


def content_key(message: Message) -> str:
    if message.kind == "work":
        identity = f"work:v3:{message.data['work_id']}"
    elif message.kind == "media":
        parsed = urlparse(message.data["url"])
        identity = f"media:{parsed.hostname}:{parsed.path}"
    else:
        identity = f"link:v3:{message.data['url']}"
    return hashlib.sha256(identity.encode()).hexdigest()
