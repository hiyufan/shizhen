"""站长配置的全站公告。

放在吸顶导航之前、随页面滚走，不占用手机上本就有限的顶部空间。

公告由服务端渲染，关闭状态记在 Cookie 里：页面先渲染、再由脚本隐藏会闪一下并推动布局。
编号取自公告内容，改了文案就是新公告，关过旧公告的访客也会看到。
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import Request

COOKIE = "qyt_notice"
_BEIJING = timezone(timedelta(hours=8))
log = logging.getLogger(__name__)


def _until(value: str) -> datetime | None:
    """不带时区的时间按北京时间理解；写错时记日志并当作不过期，不能让一处笔误拖垮整站启动。"""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        log.warning("PARSE_VIDEO_NOTICE_UNTIL 不是 ISO 时间，公告不会自动过期：%r", value)
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=_BEIJING)


def _link(value: str) -> str:
    # 只接受站内路径或 HTTPS，避免 javascript: 之类的地址进入页面
    return value if value.startswith(("https://", "/")) and not value.startswith("//") else ""


TEXT = {
    "zh": os.environ.get("PARSE_VIDEO_NOTICE", "").strip(),
    "en": os.environ.get("PARSE_VIDEO_NOTICE_EN", "").strip(),
}
LINK = _link(os.environ.get("PARSE_VIDEO_NOTICE_LINK", "").strip())
LEVEL = "warn" if os.environ.get("PARSE_VIDEO_NOTICE_LEVEL", "").strip() == "warn" else "info"
UNTIL = _until(os.environ.get("PARSE_VIDEO_NOTICE_UNTIL", "").strip())
ID = hashlib.sha256("\n".join((TEXT["zh"], TEXT["en"], LINK)).encode()).hexdigest()[:12]


def current(request: Request, locale: str) -> dict | None:
    """当前语言有文案、未过期且访客没关过时返回公告；英文未配置时英文页不显示中文公告。"""
    text = TEXT.get(locale, "")
    if not text or (UNTIL and datetime.now(timezone.utc) >= UNTIL) or request.cookies.get(COOKIE) == ID:
        return None
    return {"id": ID, "text": text, "link": LINK, "level": LEVEL}
